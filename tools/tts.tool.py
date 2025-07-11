import asyncio
import base64
import io
import json
import logging
import numpy as np
import os
import soundfile
import tempfile
import wave
from config import Config
from gemini_utils import sanitize_response_for_logging
from google.genai import client as genai_client
from google.genai import types
from tools import BaseTool
from tools import ToolContext
from typing import Any
from typing import Dict
from typing import List as TypingList
from typing import Optional
from typing import Tuple
logger = logging.getLogger("Bard")
class TTSGenerator:
    """Generates speech audio using Gemini TTS and converts it to OGG Opus."""
    @staticmethod
    def _get_audio_duration_and_waveform(audio_bytes: bytes, max_waveform_points: int = 100) -> Tuple[float, str]:
        """
        Gets audio duration from OGG Opus bytes and generates a base64 encoded waveform string.
        Waveform is a series of 0-255 values.
        """
        try:
            with io.BytesIO(audio_bytes) as audio_io:
                logger.info(f"🎤 TTSTool: Audio bytes length: {len(audio_bytes)}")
                audio_data_samplerate = soundfile.read(audio_io)
                if audio_data_samplerate is None:
                    logger.error("❌ soundfile.read returned None.")
                    return 1.0, Config.WAVEFORM_PLACEHOLDER
                audio_data, samplerate = audio_data_samplerate
            duration_secs = len(audio_data) / float(samplerate)
            mono_audio_data = np.mean(audio_data, axis=1) if audio_data.ndim > 1 else audio_data
            num_samples = len(mono_audio_data)
            if num_samples == 0:
                return duration_secs, Config.WAVEFORM_PLACEHOLDER
            if not np.issubdtype(mono_audio_data.dtype, np.floating):
                mono_audio_data = mono_audio_data.astype(np.float32)
            max_abs_val = np.max(np.abs(mono_audio_data))
            if max_abs_val > 1.0:
                mono_audio_data = mono_audio_data / max_abs_val
            step = max(1, num_samples // max_waveform_points)
            waveform_raw_bytes = bytearray()
            for i in range(0, num_samples, step):
                chunk = mono_audio_data[i:i+step]
                if len(chunk) == 0: continue
                rms_amplitude = np.sqrt(np.mean(chunk**2))
                scaled_value = int(min(max(0, rms_amplitude * 3.0), 1.0) * 99)
                waveform_raw_bytes.append(scaled_value)
            if not waveform_raw_bytes:
                return duration_secs, Config.WAVEFORM_PLACEHOLDER
            waveform_b64 = base64.b64encode(waveform_raw_bytes).decode('utf-8')
            return duration_secs, waveform_b64
        except Exception as e:
            logger.error(f"❌ Error getting duration/waveform from audio bytes.\nError:\n{e}", exc_info=True)
            try:
                with io.BytesIO(audio_bytes) as audio_io_fallback:
                    info = soundfile.info(audio_io_fallback)
                return info.duration, Config.WAVEFORM_PLACEHOLDER
            except Exception as e_info:
                logger.error(f"❌ Fallback to get duration from audio bytes also failed.\nError:\n{e_info}", exc_info=True)
                return 1.0, Config.WAVEFORM_PLACEHOLDER
    @staticmethod
    async def _feed_ffmpeg_stdin(
        ffmpeg_process: asyncio.subprocess.Process,
        gemini_response: types.GenerateContentResponse,
        model_id: str
    ) -> int:
        """
        Asynchronously feeds audio parts from the Gemini response to ffmpeg's stdin.
        Returns the total number of bytes written.
        """
        bytes_written = 0
        try:
            if not (gemini_response.candidates and
                    gemini_response.candidates[0].content and
                    gemini_response.candidates[0].content.parts):
                logger.error(f"🎤 No audio data parts found in the Gemini response for model {model_id}.")
                return 0
            for part in gemini_response.candidates[0].content.parts:
                if part.inline_data and part.inline_data.data:
                    pcm_chunk = part.inline_data.data
                    if ffmpeg_process.stdin:
                        ffmpeg_process.stdin.write(pcm_chunk)
                        await ffmpeg_process.stdin.drain()
                        bytes_written += len(pcm_chunk)
                    else:
                        logger.warning("🎤 ffmpeg stdin is None, cannot write TTS chunk.")
                        break
                else:
                    logger.info("🎤 Encountered a part without inline_data.data in Gemini response.")
            if bytes_written == 0:
                logger.warning(f"🎤 No PCM data was actually written to ffmpeg for model {model_id} from the provided Gemini response.")
        except Exception as e:
            logger.error(f"❌ Error while feeding TTS data from Gemini response parts to ffmpeg for model {model_id}.\nError:\n{e}", exc_info=True)
            raise
        finally:
            if ffmpeg_process.stdin and not ffmpeg_process.stdin.is_closing():
                try:
                    ffmpeg_process.stdin.close()
                except BrokenPipeError:
                    logger.warning("🎤 ffmpeg stdin closed prematurely (BrokenPipeError) while trying to close it. This might be normal if ffmpeg exited.")
                except Exception as e_close:
                    logger.warning(f"🎤 Error closing ffmpeg stdin: {e_close}")
        return bytes_written
    @staticmethod
    async def _read_ffmpeg_stdout(ffmpeg_process: asyncio.subprocess.Process) -> bytes:
        """Asynchronously reads all data from ffmpeg's stdout."""
        ogg_chunks = []
        try:
            while True:
                if ffmpeg_process.stdout:
                    chunk = await ffmpeg_process.stdout.read(4096)
                    if not chunk:
                        break
                    ogg_chunks.append(chunk)
                else:
                    logger.warning("🎤 ffmpeg stdout is None, cannot read converted audio.")
                    break
        except Exception as e:
            logger.error(f"❌ Error while reading OGG data from ffmpeg stdout.\nError:\n{e}", exc_info=True)
            raise
        return b"".join(ogg_chunks)
    @staticmethod
    async def generate_speech_ogg(
        gemini_client: genai_client.Client,
        text_for_tts: str,
        style: Optional[str] = None
    ) -> Optional[Tuple[bytes, float, str]]:
        logger.info("🎤 TTSTool: Entering generate_speech_ogg")
        """
        Generates speech audio in OGG Opus format from text using Gemini TTS,
        feeding PCM data from the API response directly to ffmpeg for conversion.
        """
        if not gemini_client:
            logger.error("❌ Gemini client not initialized. Cannot generate TTS.")
            logger.info("🎤 TTSTool: Gemini client not initialized.")
            return None
        voice_style_info = f" Style: {style}," if style else ""
        log_message = f"🎤 TTSTool: Performing secondary Gemini call for TTS generation. Voice: {Config.VOICE_NAME} {voice_style_info} Model: {Config.MODEL_ID_TTS}\nText:\n'{text_for_tts}"
        logger.info(log_message)
        voice_config_params = {"prebuilt_voice_config": types.PrebuiltVoiceConfig(voice_name=Config.VOICE_NAME)}
        speech_generation_config = types.GenerateContentConfig(
            response_modalities=["AUDIO"],
            speech_config=types.SpeechConfig(
                voice_config=types.VoiceConfig(**voice_config_params)
            )
        )
        pcm_format = 's16le'
        if Config.TTS_SAMPLE_WIDTH == 1:
            pcm_format = 'u8'
        elif Config.TTS_SAMPLE_WIDTH != 2:
            logger.error(f"❌ Unsupported TTS_SAMPLE_WIDTH: {Config.TTS_SAMPLE_WIDTH}. Defaulting to s16le, this may cause issues.")
        ffmpeg_input_args = [
            '-f', pcm_format,
            '-ar', str(Config.TTS_SAMPLE_RATE),
            '-ac', str(Config.TTS_CHANNELS),
            '-i', '-'
        ]
        ffmpeg_output_args = [
            '-c:a', 'libopus',
            '-b:a', '32k',
            '-ar', '48000',
            '-ac', '1',
            '-application', 'voip',
            '-vbr', 'on',
            '-f', 'opus',
            'pipe:1'
        ]
        ffmpeg_command = [Config.FFMPEG_PATH, '-y'] + ffmpeg_input_args + ffmpeg_output_args
        ffmpeg_process = None
        try:
            logger.info(f"🎤 TTSTool: Creating ffmpeg process with command: {ffmpeg_command}")
            ffmpeg_process = await asyncio.create_subprocess_exec(
                *ffmpeg_command,
                stdin=asyncio.subprocess.PIPE,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE
            )
            logger.info("🎤 TTSTool: ffmpeg process created.")
            request_payload = sanitize_response_for_logging({
                "model": Config.MODEL_ID_TTS,
                "contents": text_for_tts,
                "config": speech_generation_config.dict()
            })
            logger.info(f"REQUEST to Gemini API (TTS):\n{json.dumps(request_payload, indent=2)}")
            logger.info("🎤 TTSTool: Sending request to Gemini API for TTS generation.")
            gemini_response_object = await gemini_client.aio.models.generate_content(
                model=Config.MODEL_ID_TTS,
                contents=text_for_tts,
                config=speech_generation_config
            )
            logger.info("🎤 TTSTool: Received response from Gemini API for TTS generation.")
            sanitized_response = sanitize_response_for_logging(gemini_response_object.dict())
            logger.info(f"RESPONSE from Gemini API (TTS):\n{json.dumps(sanitized_response, indent=2)}")
            reason = "Unknown"
            details = ""
            candidate = None

            if not gemini_response_object.candidates:
                reason = "No candidates returned"
                if gemini_response_object.prompt_feedback:
                    details = f"Prompt Feedback: {gemini_response_object.prompt_feedback}"
            else:
                candidate = gemini_response_object.candidates[0]
                finish_reason = candidate.finish_reason

                if finish_reason is None:
                    reason = "No finish reason provided"
                elif finish_reason.name != "STOP":
                    reason = finish_reason.name
                    if finish_reason.name == "SAFETY":
                        details = f"Safety Ratings: {candidate.safety_ratings}"

            if candidate is None or (candidate.finish_reason is None or candidate.finish_reason.name != "STOP"):
                logger.error(f"❌ TTS generation stopped by API. Finish Reason: {reason}. {details}")
                if ffmpeg_process and ffmpeg_process.returncode is None:
                    ffmpeg_process.terminate()
                    await ffmpeg_process.wait()
                return None
            feed_task = asyncio.create_task(
                TTSGenerator._feed_ffmpeg_stdin(ffmpeg_process, gemini_response_object, Config.MODEL_ID_TTS)
            )
            read_task = asyncio.create_task(
                TTSGenerator._read_ffmpeg_stdout(ffmpeg_process)
            )
            bytes_fed_to_ffmpeg, ogg_opus_bytes = await asyncio.gather(feed_task, read_task)
            logger.info("🎤 TTSTool: Finished feeding data to ffmpeg and reading output.")
            return_code = await ffmpeg_process.wait()
            if return_code != 0:
                stderr_data = await ffmpeg_process.stderr.read() if ffmpeg_process.stderr else b''
                logger.error(f"❌ ffmpeg conversion failed with return code {return_code}.\n"
                             f"Command: {' '.join(ffmpeg_command)}\n"
                             f"Stderr:\n{stderr_data.decode(errors='ignore')}")
                return None
            if not ogg_opus_bytes:
                logger.error("❌ No OGG Opus data was produced by ffmpeg, though ffmpeg exited cleanly. Check if PCM data was fed.")
                if bytes_fed_to_ffmpeg == 0:
                    logger.error("❌ Confirmation: 0 bytes of PCM data were fed to ffmpeg.")
                return None
            duration_secs, waveform_b64 = TTSGenerator._get_audio_duration_and_waveform(ogg_opus_bytes)
            logger.info("🎤 TTSTool: Successfully generated audio duration and waveform.")
            return ogg_opus_bytes, duration_secs, waveform_b64
        except FileNotFoundError:
             logger.error(f"❌ ffmpeg command not found. Ensure FFMPEG_PATH ('{Config.FFMPEG_PATH}') is correct and ffmpeg is installed.")
             return None
        except Exception as e:
            logger.error(f"❌ TTS generation or OGG conversion pipeline error.\nError:\n{e}", exc_info=True)
            if ffmpeg_process and ffmpeg_process.returncode is None:
                logger.info("🎤 TTSTool: Killing ffmpeg process due to exception.")
                ffmpeg_process.kill()
                await ffmpeg_process.wait()
            return None
class TTSTool(BaseTool):
    def __init__(self, config: Config):
        self.config = config
    def get_function_declarations(self) -> TypingList[types.FunctionDeclaration]:
        return [
            types.FunctionDeclaration(
                name="speak_message",
                description=(
                    "Purpose: generate speech using a text input. "
                    "Results: generated audio file. "
                    "Restrictions: only use this once after as the last tool if the user requests audio or a spoken message."
                ),
                parameters=types.Schema(
                    type=types.Type.OBJECT,
                    properties={
                        "text_to_speak": types.Schema(
                            type=types.Type.STRING,
                            description="Provide the exact text you want spoken. This text will NOT appear in your chat reply to the user."
                        ),
                        "style": types.Schema(
                            type=types.Type.STRING,
                            description=(
                                "Optional. Specify a speaking style. If omitted, a neutral voice is used."
                            )
                        ),
                    },
                    required=["text_to_speak"],
                )
            )
        ]
    async def execute_tool(self, function_name: str, args: Dict[str, Any], context: ToolContext) -> types.Part:
        if function_name != "speak_message":
            return types.Part(function_response=types.FunctionResponse(
                name=function_name,
                response={"success": False, "error": f"Unknown function in TTSTool: {function_name}"}
            ))
        gemini_client = context.get("gemini_client")
        if not gemini_client:
            logger.error("❌ TTSTool: gemini_client not found in context.")
            return types.Part(function_response=types.FunctionResponse(
                name=function_name,
                response={"success": False, "error": "Gemini client not available for TTS."}
            ))
        text_to_speak_arg = args.get("text_to_speak")
        style_arg = args.get("style")
        if not text_to_speak_arg:
            logger.warning("🎤 'speak_message' called without 'text_to_speak'.")
            return types.Part(function_response=types.FunctionResponse(
                name=function_name,
                response={"success": False, "error": "Missing 'text_to_speak' argument."}
            ))
        tts_prompt_text = text_to_speak_arg
        if style_arg:
            safe_style = ''.join(filter(str.isalnum, str(style_arg))).lower()
            if safe_style:
                tts_prompt_text = f"In a {safe_style} tone, say: {text_to_speak_arg}"
            else:
                logger.warning(f"🎤 Style argument '{style_arg}' was sanitized to empty. Using neutral tone.")
        tts_result = await TTSGenerator.generate_speech_ogg(gemini_client, tts_prompt_text, style=style_arg)
        if tts_result:
            ogg_audio_data, audio_duration, audio_waveform_b64 = tts_result
            context.audio_data = ogg_audio_data
            context.audio_duration = audio_duration
            context.audio_waveform = audio_waveform_b64
            context.is_final_output = True
            return types.Part(function_response=types.FunctionResponse(
                name=function_name,
                response={"success": True, "action": "speech_generated", "duration_seconds": audio_duration}
            ))
        else:
            logger.warning(f"🎤 TTS failed for 'speak_message'. Intended text: '{text_to_speak_arg}' with style '{style_arg}'")
            return types.Part(function_response=types.FunctionResponse(
                name=function_name,
                response={"success": False, "error": "TTS generation failed."}
            ))
