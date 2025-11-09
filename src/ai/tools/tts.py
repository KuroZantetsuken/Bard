import asyncio
import base64
import io
import logging
from typing import Any, AsyncGenerator, Dict, List, Optional, Tuple

import numpy as np
import soundfile
from google.genai import types

from ai.tools.base import BaseTool, ToolContext
from settings import Settings

log = logging.getLogger("Bard")

DEFAULT_WAVEFORM = "FzYACgAAAAAAACQAAAAAAAA="


async def _execute_ffmpeg(
    arguments: List[str], input_data: Optional[bytes] = None, timeout: float = 30.0
) -> Tuple[Optional[bytes], Optional[bytes], int]:
    """
    Executes an FFmpeg command with specified arguments and optional input data.
    """
    log.debug("Executing FFmpeg", extra={"arguments": arguments})
    try:
        process = await asyncio.create_subprocess_exec(
            *arguments,
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        try:
            stdout_data, stderr_data = await asyncio.wait_for(
                process.communicate(input=input_data), timeout=timeout
            )
        except asyncio.TimeoutError:
            log.debug(f"FFmpeg process timed out after {timeout} seconds.")
            process.kill()
            _, stderr_data_after_kill = await process.communicate()
            error_message = b"Process timed out. " + (stderr_data_after_kill or b"")
            return None, error_message.strip(), -1
        return_code = process.returncode if process.returncode is not None else -1
        return stdout_data, stderr_data, return_code
    except FileNotFoundError:
        log.critical(
            f"FFmpeg executable not found: '{arguments[0]}'. Ensure it's installed and in PATH."
        )
        return None, b"FFmpeg not found", -1
    except Exception as e:
        log.critical(f"FFmpeg execution failed: {str(e)}")
        return None, str(e).encode(), -1


async def _convert_audio(
    input_data: bytes,
    input_format: str,
    output_format: str,
    input_args: List[str] = [],
    output_args: List[str] = [],
    timeout: float = 30.0,
) -> Optional[bytes]:
    """
    Converts audio data between specified formats using FFmpeg.
    """
    args = [
        Settings.FFMPEG_PATH,
        "-y",
        "-f",
        input_format,
        *input_args,
        "-i",
        "-",
        "-f",
        output_format,
        *output_args,
        "-",
    ]
    stdout, stderr, return_code = await _execute_ffmpeg(args, input_data, timeout)
    if return_code == 0 and stdout:
        return stdout
    error_msg = stderr.decode(errors="ignore") if stderr else "Unknown error"
    log.error(f"Audio conversion failed with code {return_code}: {error_msg}")
    return None


def _get_audio_duration_and_waveform(
    audio_bytes: bytes, max_waveform_points: int = 100
) -> Tuple[float, str]:
    """
    Calculates the audio duration from OGG Opus bytes and generates a base64 encoded waveform string.
    The waveform is a series of 0-255 values representing audio amplitude.

    Args:
        audio_bytes: The audio data in OGG Opus format.
        max_waveform_points: The maximum number of points to represent the waveform.

    Returns:
        A tuple containing the duration of the audio in seconds and the base64 encoded waveform string.
    """

    log.debug("Getting audio duration and waveform")
    try:
        with io.BytesIO(audio_bytes) as audio_io:
            read_result = soundfile.read(audio_io)
            if read_result is None:
                log.error("soundfile.read returned None.")
                return 1.0, DEFAULT_WAVEFORM
            audio_data, samplerate = read_result

        if audio_data is None or samplerate is None:
            log.error("soundfile.read returned None for audio_data or samplerate.")
            return 1.0, DEFAULT_WAVEFORM

        duration_secs = len(audio_data) / float(samplerate)
        mono_audio_data = (
            np.mean(audio_data, axis=1) if audio_data.ndim > 1 else audio_data
        )
        num_samples = len(mono_audio_data)
        if num_samples == 0:
            return duration_secs, DEFAULT_WAVEFORM
        if not np.issubdtype(mono_audio_data.dtype, np.floating):
            mono_audio_data = mono_audio_data.astype(np.float32)
        max_abs_val = np.max(np.abs(mono_audio_data))
        if max_abs_val > 1.0:
            mono_audio_data = mono_audio_data / max_abs_val
        step = max(1, num_samples // max_waveform_points)
        waveform_raw_bytes = bytearray()
        for i in range(0, num_samples, step):
            chunk = mono_audio_data[i : i + step]
            if len(chunk) == 0:
                continue
            rms_amplitude = np.sqrt(np.mean(chunk**2))
            scaled_value = int(min(max(0, rms_amplitude * 3.0), 1.0) * 99)
            waveform_raw_bytes.append(scaled_value)
        if not waveform_raw_bytes:
            return duration_secs, DEFAULT_WAVEFORM
        waveform_b64 = base64.b64encode(waveform_raw_bytes).decode("utf-8")
        log.debug(
            "Finished getting audio duration and waveform",
            extra={"duration_secs": duration_secs},
        )
        return duration_secs, waveform_b64
    except Exception as e:
        log.error(
            f"Error getting duration/waveform from audio bytes: {e}.",
            exc_info=True,
        )
        try:
            with io.BytesIO(audio_bytes) as audio_io_fallback:
                info = soundfile.info(audio_io_fallback)
            return info.duration, DEFAULT_WAVEFORM
        except Exception as e_info:
            log.error(
                f"Fallback to get duration from audio bytes also failed. Error: {e_info}",
                exc_info=True,
            )
            return 1.0, DEFAULT_WAVEFORM


class TTSGenerator(BaseTool):
    """
    Generates speech audio using the Gemini Text-to-Speech (TTS) API and converts it to OGG Opus format.
    It also calculates audio duration and generates a waveform for visual representation.
    """

    tool_emoji = "🗣️"

    def __init__(self, context: ToolContext):
        """
        Initializes the TTSGenerator.

        Args:
            context: The ToolContext object providing shared resources.
        """
        super().__init__(context)
        self.gemini_core = context.gemini_core

    async def synthesize(self, text: str, voice_id: str) -> AsyncGenerator[bytes, None]:
        """
        Synthesizes speech from text using the Gemini TTS API and yields audio chunks.

        Args:
            text: The text to convert to speech.
            voice_id: The ID of the voice to use for synthesis.

        Yields:
            Audio data chunks in bytes.
        """
        log.debug(
            "Synthesizing speech", extra={"text_len": len(text), "voice_id": voice_id}
        )
        try:
            voice_config_params = {
                "prebuilt_voice_config": types.PrebuiltVoiceConfig(voice_name=voice_id)
            }
            speech_generation_config = types.GenerateContentConfig(
                response_modalities=["AUDIO"],
                speech_config=types.SpeechConfig(
                    voice_config=types.VoiceConfig(**voice_config_params)
                ),
            )
            log.debug(
                "Sending TTS synthesis request to Gemini",
                extra={
                    "model": Settings.MODEL_ID_TTS,
                    "contents": [
                        types.Content(parts=[types.Part(text=text)]).model_dump()
                    ],
                    "config": speech_generation_config.model_dump(),
                },
            )
            async for chunk in await self.gemini_core.generate_content(
                model=Settings.MODEL_ID_TTS,
                contents=[types.Content(parts=[types.Part(text=text)])],
                config=speech_generation_config,
                stream=True,
            ):
                log.debug(
                    "Received TTS synthesis response chunk from Gemini",
                    extra={"chunk": chunk.model_dump()},
                )
                if (
                    chunk.candidates
                    and chunk.candidates[0].content
                    and chunk.candidates[0].content.parts
                    and chunk.candidates[0].content.parts[0].inline_data
                    and chunk.candidates[0].content.parts[0].inline_data.data
                ):
                    yield chunk.candidates[0].content.parts[0].inline_data.data
        except Exception as e:
            log.error(f"Error during TTS synthesis: {e}.", exc_info=True)
            raise

    async def generate_speech_ogg(
        self, text_for_tts: str, style: Optional[str] = None
    ) -> Optional[Tuple[bytes, float, str]]:
        """
        Generates speech audio in OGG Opus format from text using Gemini TTS.
        It converts the PCM data from the API response directly to OGG Opus using FFmpeg.

        Args:
            text_for_tts: The text to convert to speech.
            style: Optional style for the voice (e.g., tone, emotion).

        Returns:
            A tuple containing the OGG Opus audio bytes, its duration in seconds,
            and a base64 encoded waveform string, or None if generation fails.
        """
        log.debug(
            "Generating speech OGG",
            extra={"text_len": len(text_for_tts), "style": style},
        )
        if not self.gemini_core:
            log.error("Gemini core not initialized. Cannot generate TTS.")
            return None

        if style:
            text_for_tts = f"{style}: {text_for_tts}"
            log.info(
                f"Applying style '{style}'. Modified text for TTS: '{text_for_tts}'"
            )

        voice_config_params = {
            "prebuilt_voice_config": types.PrebuiltVoiceConfig(
                voice_name=Settings.VOICE_NAME
            )
        }
        speech_generation_config = types.GenerateContentConfig(
            response_modalities=["AUDIO"],
            speech_config=types.SpeechConfig(
                voice_config=types.VoiceConfig(**voice_config_params)
            ),
        )

        log.debug(
            "Sending TTS generation request to Gemini",
            extra={
                "model": Settings.MODEL_ID_TTS,
                "contents": [
                    types.Content(parts=[types.Part(text=text_for_tts)]).model_dump()
                ],
                "config": speech_generation_config.model_dump(),
            },
        )

        gemini_response_object = await self.gemini_core.generate_content(
            model=Settings.MODEL_ID_TTS,
            contents=[types.Content(parts=[types.Part(text=text_for_tts)])],
            config=speech_generation_config,
        )
        log.debug(
            "Received TTS generation response from Gemini",
            extra={"response": gemini_response_object.model_dump()},
        )

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
        if candidate is None or (
            candidate.finish_reason is None or candidate.finish_reason.name != "STOP"
        ):
            log.error(
                f"TTS generation stopped by API. Finish Reason: {reason}. Details: {details}."
            )
            return None
        pcm_data_chunks = []
        if not (
            gemini_response_object.candidates
            and gemini_response_object.candidates[0].content
            and gemini_response_object.candidates[0].content.parts
        ):
            log.error(
                f"No audio data parts found in the Gemini response for model {Settings.MODEL_ID_TTS}."
            )
            return None
        for part in gemini_response_object.candidates[0].content.parts:
            if part.inline_data and part.inline_data.data:
                pcm_data_chunks.append(part.inline_data.data)
            else:
                log.debug(
                    "Encountered a part without inline_data.data in Gemini response."
                )
        if not pcm_data_chunks:
            log.warning(
                f"No PCM data was received from Gemini for model {Settings.MODEL_ID_TTS}."
            )
            return None
        pcm_data = b"".join(pcm_data_chunks)
        pcm_format = "s16le"
        input_args = [
            "-ar",
            "24000",
            "-ac",
            "1",
        ]
        output_args = [
            "-c:a",
            "libopus",
            "-b:a",
            "64k",
            "-ar",
            "48000",
            "-ac",
            "1",
            "-application",
            "voip",
            "-vbr",
            "on",
        ]
        try:
            ogg_opus_bytes = await _convert_audio(
                input_data=pcm_data,
                input_format=pcm_format,
                output_format="opus",
                input_args=input_args,
                output_args=output_args,
            )
            if not ogg_opus_bytes:
                log.error("FFmpeg conversion produced no output.")
                return None
        except Exception as e:
            log.error(f"FFmpeg conversion failed: {e}.", exc_info=True)
            return None
        duration_secs, waveform_b64 = _get_audio_duration_and_waveform(
            ogg_opus_bytes, max_waveform_points=256
        )
        log.info(f"Successfully generated speech audio ({duration_secs:.2f}s)")
        return ogg_opus_bytes, duration_secs, waveform_b64

    def get_function_declarations(self) -> List[types.FunctionDeclaration]:
        """
        Returns the function declarations for the `generate_speech_ogg` function.
        This function is exposed to the Gemini model to allow it to generate speech.
        """
        return [
            types.FunctionDeclaration(
                name="generate_speech_ogg",
                description="Purpose: This tool serves to transform textual responses into natural-sounding speech, enabling the AI to deliver audible output. This functionality is particularly beneficial for voice-based interactions, enhancing accessibility, and providing a more dynamic user experience. Arguments: `text_for_tts` (string, mandatory) is the exact text to convert into speech. `style` (string, optional) influences vocal characteristics (e.g., tone, emotion) if supported by the underlying TTS model. Results: The function returns the generated speech audio in OGG Opus format, along with its duration in seconds and a base64-encoded waveform string. These results are essential for seamless integration into platforms that support native voice messages, such as Discord, allowing for accurate display of playback length and visual representation of the audio. Notably, the `waveform` generation for Discord native voice messages accurately produces approximately 256 datapoints. Restrictions/Guidelines: Use this tool when the user explicitly requests an audio response, when a response is intended to be spoken rather than read, or in conversational contexts where an audible reply significantly enhances the user experience. When this tool is invoked, any accompanying textual response from the AI will be used as a caption for the audio message; if no text is generated by the AI, no text will be sent. Do not use for purely textual responses or when audio output is unnecessary or redundant.",
                parameters=types.Schema(
                    type=types.Type.OBJECT,
                    properties={
                        "text_for_tts": types.Schema(
                            type=types.Type.STRING,
                            description="The text to convert to speech.",
                        ),
                        "style": types.Schema(
                            type=types.Type.STRING,
                            description="Optional style for the voice.",
                        ),
                    },
                    required=["text_for_tts"],
                ),
            )
        ]

    async def execute_tool(
        self, function_name: str, args: Dict[str, Any], context: ToolContext
    ) -> types.Part:
        """
        Executes the `generate_speech_ogg` function.

        Args:
            function_name: The name of the function to execute (expected to be "generate_speech_ogg").
            args: A dictionary containing the `text_for_tts` and optional `style` arguments.
            context: The ToolContext object providing shared resources.

        Returns:
            A Gemini types.Part object containing the function response, including
            success status, duration, waveform, and a message.
        """
        log.info(f"Executing tool '{function_name}'")
        log.debug("Tool arguments", extra={"tool_args": args})
        if function_name == "generate_speech_ogg":
            text_for_tts = args.get("text_for_tts")
            style = args.get("style")
            if not text_for_tts:
                log.warning("Missing 'text_for_tts' argument for generate_speech_ogg.")
                return types.Part(
                    function_response=types.FunctionResponse(
                        name=function_name,
                        response={
                            "success": False,
                            "error": "Missing 'text_for_tts' argument for generate_speech_ogg.",
                        },
                    )
                )
            try:
                result = await self.generate_speech_ogg(text_for_tts, style)
                if result:
                    ogg_opus_bytes, duration_secs, waveform_b64 = result
                    context.tool_response_data["audio_bytes"] = ogg_opus_bytes
                    return types.Part(
                        function_response=types.FunctionResponse(
                            name=function_name,
                            response={
                                "success": True,
                                "duration_secs": duration_secs,
                                "waveform_b64": waveform_b64,
                                "message": "Speech generated successfully.",
                            },
                        )
                    )
                else:
                    log.error("Failed to generate speech audio.")
                    return types.Part(
                        function_response=types.FunctionResponse(
                            name=function_name,
                            response={
                                "success": False,
                                "error": "Failed to generate speech audio.",
                            },
                        )
                    )
            except Exception as e:
                log.error(
                    f"An error occurred during speech generation: {str(e)}.",
                    exc_info=True,
                )
                return types.Part(
                    function_response=types.FunctionResponse(
                        name=function_name,
                        response={
                            "success": False,
                            "error": f"An error occurred during speech generation: {str(e)}",
                        },
                    )
                )
        else:
            return types.Part(
                function_response=types.FunctionResponse(
                    name=function_name,
                    response={
                        "success": False,
                        "error": f"Unknown function: {function_name}",
                    },
                )
            )
