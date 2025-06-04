import wave
import tempfile
import soundfile
import os
import numpy as np
import logging
import base64
import asyncio
from typing import Any, Dict, List as TypingList, Tuple, Optional
from tools import BaseTool, ToolContext
from google.genai import types
from google.genai import client as genai_client
from config import Config
logger = logging.getLogger("Bard")
class TTSGenerator:
    """Generates speech audio using Gemini TTS and converts it to OGG Opus."""
    @staticmethod
    async def _convert_to_ogg_opus(input_wav_path: str, output_ogg_path: str) -> bool:
        """Converts a WAV file to OGG Opus format using ffmpeg."""
        try:
            command = [
                Config.FFMPEG_PATH, '-y', '-i', input_wav_path,
                '-c:a', 'libopus', '-b:a', '32k',
                '-ar', '48000',
                '-ac', '1',
                '-application', 'voip',
                '-vbr', 'on',
                output_ogg_path
            ]
            process = await asyncio.create_subprocess_exec(
                *command, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE
            )
            stdout, stderr = await process.communicate()
            if process.returncode != 0:
                stdout_decoded = stdout.decode(errors='ignore')
                stderr_decoded = stderr.decode(errors='ignore')
                logger.error(f"❌ ffmpeg conversion failed for input WAV.\nInput Path:\n{input_wav_path}\nReturn Code: {process.returncode}\nStdout:\n{stdout_decoded}\nStderr:\n{stderr_decoded}")
                return False
            return True
        except FileNotFoundError:
            logger.error(f"❌ ffmpeg command not found. Ensure FFMPEG_PATH ('{Config.FFMPEG_PATH}') is correct and ffmpeg is installed.")
            return False
        except Exception as e:
            logger.error(f"❌ Error during ffmpeg conversion.\nInput WAV:\n{input_wav_path}\nError:\n{e}", exc_info=True)
            return False
    @staticmethod
    def _get_audio_duration_and_waveform(audio_path: str, max_waveform_points: int = 100) -> Tuple[float, str]:
        """
        Gets audio duration and generates a base64 encoded waveform string suitable for Discord.
        Waveform is a series of 0-255 values.
        """
        try:
            audio_data, samplerate = soundfile.read(audio_path)
            duration_secs = len(audio_data) / float(samplerate)
            mono_audio_data = np.mean(audio_data, axis=1) if audio_data.ndim > 1 else audio_data
            num_samples = len(mono_audio_data)
            if num_samples == 0:
                return duration_secs, Config.WAVEFORM_PLACEHOLDER
            if np.issubdtype(mono_audio_data.dtype, np.integer):
                 mono_audio_data = mono_audio_data / np.iinfo(mono_audio_data.dtype).max
            elif np.issubdtype(mono_audio_data.dtype, np.floating) and np.max(np.abs(mono_audio_data)) > 1.0:
                mono_audio_data = mono_audio_data / np.max(np.abs(mono_audio_data))
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
            logger.error(f"❌ Error getting duration/waveform for audio file.\nFile:\n{audio_path}\nError:\n{e}", exc_info=True)
            try:
                info = soundfile.info(audio_path)
                return info.duration, Config.WAVEFORM_PLACEHOLDER
            except Exception as e_info:
                logger.error(f"❌ Fallback to get duration also failed for audio file.\nFile:\n{audio_path}\nError:\n{e_info}", exc_info=True)
                return 1.0, Config.WAVEFORM_PLACEHOLDER
    @staticmethod
    async def generate_speech_ogg(
        gemini_client_instance: genai_client.Client,
        text_for_tts: str,
        style: Optional[str] = None
    ) -> Optional[Tuple[bytes, float, str]]:
        """Generates speech audio in OGG Opus format from text using Gemini TTS."""
        if not gemini_client_instance:
            logger.error("❌ Gemini client not initialized. Cannot generate TTS.")
            return None
        tmp_wav_path, tmp_ogg_path = None, None
        try:
            voice_style_info = f" Style: {style}," if style else ""
            logger.info(f"🎤 Generating TTS (WAV) with details:\nText:\n'{text_for_tts}'\nVoice: {Config.VOICE_NAME},{voice_style_info} Model: {Config.MODEL_ID_TTS}")
            voice_config_params = {"prebuilt_voice_config": types.PrebuiltVoiceConfig(voice_name=Config.VOICE_NAME)}
            if style:
                pass
            speech_generation_config = types.GenerateContentConfig(
                response_modalities=["AUDIO"],
                speech_config=types.SpeechConfig(
                    voice_config=types.VoiceConfig(**voice_config_params)
                )
            )
            response = await gemini_client_instance.aio.models.generate_content(
                model=Config.MODEL_ID_TTS,
                contents=text_for_tts,
                config=speech_generation_config
            )
            wav_data = None
            if (response.candidates and response.candidates[0].content and
                response.candidates[0].content.parts and
                response.candidates[0].content.parts[0].inline_data and
                response.candidates[0].content.parts[0].inline_data.data):
                wav_data = response.candidates[0].content.parts[0].inline_data.data
            if not wav_data:
                logger.error("❌ No WAV audio data extracted from Gemini TTS response.")
                return None
            with tempfile.NamedTemporaryFile(suffix='.wav', delete=False) as tmp_wav_file_obj, \
                 tempfile.NamedTemporaryFile(suffix='.ogg', delete=False) as tmp_ogg_file_obj:
                tmp_wav_path = tmp_wav_file_obj.name
                tmp_ogg_path = tmp_ogg_file_obj.name
            try:
                with wave.open(tmp_wav_path, 'wb') as wf:
                    wf.setnchannels(Config.TTS_CHANNELS)
                    wf.setsampwidth(Config.TTS_SAMPLE_WIDTH)
                    wf.setframerate(Config.TTS_SAMPLE_RATE)
                    wf.writeframes(wav_data)
                if not await TTSGenerator._convert_to_ogg_opus(tmp_wav_path, tmp_ogg_path):
                    return None
                duration_secs, waveform_b64 = TTSGenerator._get_audio_duration_and_waveform(tmp_ogg_path)
                with open(tmp_ogg_path, 'rb') as f_ogg:
                    ogg_opus_bytes = f_ogg.read()
                logger.info(f"🎤 OGG Opus generated successfully. Size: {len(ogg_opus_bytes)} bytes, Duration: {duration_secs:.2f}s.")
                return ogg_opus_bytes, duration_secs, waveform_b64
            finally:
                for f_path in [tmp_wav_path, tmp_ogg_path]:
                    if f_path and os.path.exists(f_path):
                        try:
                            os.unlink(f_path)
                        except OSError as e_unlink:
                            logger.warning(f"⚠️ Could not delete temporary file.\nFile:\n{f_path}\nError:\n{e_unlink}")
        except types.StopCandidateException as sce:
            logger.error(f"❌ TTS generation stopped by API (safety/other): {sce}", exc_info=True)
            return None
        except Exception as e:
            logger.error(f"❌ TTS generation or OGG conversion pipeline error.\nError:\n{e}", exc_info=True)
            return None
class TTSTool(BaseTool):
    def __init__(self, config: Config):
        self.config = config
    def get_function_declarations(self) -> TypingList[types.FunctionDeclaration]:
        return [
            types.FunctionDeclaration(
                name="speak_message",
                description=(
                    "Use this if the user asks for a voice/audio response, or if you decide a spoken response is best. "
                    "The text provided to be spoken will NOT appear in your chat reply to the user. "
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
                                "Optional. Specify a speaking style like \"CHEERFUL\", \"SAD\", \"ANGRY\", \"EXCITED\", "
                                "\"FRIENDLY\", \"HOPEFUL\", \"POLITE\", \"SERIOUS\", \"SOMBER\", \"WHISPERING\". "
                                "If omitted, a neutral voice is used."
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
        gemini_client_instance = context.get("gemini_client")
        if not gemini_client_instance:
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
            safe_style = style_arg.replace('_', ' ').lower()
            tts_prompt_text = f"In a {safe_style} tone, say: {text_to_speak_arg}"
        tts_result = await TTSGenerator.generate_speech_ogg(gemini_client_instance, tts_prompt_text, style=style_arg)
        if tts_result:
            ogg_audio_data, audio_duration, audio_waveform_b64 = tts_result
            context.audio_data = ogg_audio_data
            context.audio_duration = audio_duration
            context.audio_waveform = audio_waveform_b64
            logger.info(f"🎤 TTS successful for 'speak_message'. Audio data prepared.")
            return types.Part(function_response=types.FunctionResponse(
                name=function_name,
                response={"success": True, "action": "speech_generated", "duration_seconds": audio_duration}
            ))
        else:
            logger.warning(f"🎤 TTS failed for 'speak_message'. Intended text: '{text_to_speak_arg}'")
            return types.Part(function_response=types.FunctionResponse(
                name=function_name,
                response={"success": False, "error": "TTS generation failed."}
            ))