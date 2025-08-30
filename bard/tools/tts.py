import logging
from typing import Any, AsyncGenerator, Dict, List, Optional, Tuple

from google.genai import types

from bard.tools.base import BaseTool, ToolContext
from bard.util.logging import clean_dict, prettify_json_for_logging
from bard.util.media.audio import get_audio_duration_and_waveform
from config import Config

logger = logging.getLogger("Bard")


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
        self.gemini_client = context.gemini_client
        self.ffmpeg_wrapper = context.ffmpeg_wrapper

    async def synthesize(self, text: str, voice_id: str) -> AsyncGenerator[bytes, None]:
        """
        Synthesizes speech from text using the Gemini TTS API and yields audio chunks.

        Args:
            text: The text to convert to speech.
            voice_id: The ID of the voice to use for synthesis.

        Yields:
            Audio data chunks in bytes.
        """
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
            request_payload = {
                "model": Config.MODEL_ID_TTS,
                "contents": [types.Content(parts=[types.Part(text=text)]).model_dump()],
                "config": speech_generation_config.model_dump(),
            }
            logger.debug(
                f"Gemini API (tts_synthesis) request:\n{prettify_json_for_logging(clean_dict(request_payload))}"
            )
            async for chunk in await self.gemini_client.generate_content(
                model=Config.MODEL_ID_TTS,
                contents=[types.Content(parts=[types.Part(text=text)])],
                config=speech_generation_config,
                stream=True,
            ):
                logger.debug(
                    f"Gemini API (tts_synthesis) response chunk:\n{prettify_json_for_logging(clean_dict(chunk.dict()))}"
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
            logger.error(f"Error during TTS synthesis: {e}.", exc_info=True)
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
        if not self.gemini_client:
            logger.error("Gemini client not initialized. Cannot generate TTS.")
            return None

        if style:
            text_for_tts = f"{style}: {text_for_tts}"
            logger.info(
                f"Applying style '{style}'. Modified text for TTS: '{text_for_tts}'"
            )

        voice_config_params = {
            "prebuilt_voice_config": types.PrebuiltVoiceConfig(
                voice_name=Config.VOICE_NAME
            )
        }
        speech_generation_config = types.GenerateContentConfig(
            response_modalities=["AUDIO"],
            speech_config=types.SpeechConfig(
                voice_config=types.VoiceConfig(**voice_config_params)
            ),
        )

        request_payload = {
            "model": Config.MODEL_ID_TTS,
            "contents": [
                types.Content(parts=[types.Part(text=text_for_tts)]).model_dump()
            ],
            "config": speech_generation_config.model_dump(),
        }
        logger.debug(
            f"Gemini API (tts_generate_ogg) request:\n{prettify_json_for_logging(clean_dict(request_payload))}"
        )

        gemini_response_object = await self.gemini_client.generate_content(
            model=Config.MODEL_ID_TTS,
            contents=[types.Content(parts=[types.Part(text=text_for_tts)])],
            config=speech_generation_config,
        )
        logger.debug(
            f"Gemini API (tts_generate_ogg) response:\n{prettify_json_for_logging(clean_dict(gemini_response_object.model_dump()))}"
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
            logger.error(
                f"TTS generation stopped by API. Finish Reason: {reason}. Details: {details}."
            )
            return None
        pcm_data_chunks = []
        if not (
            gemini_response_object.candidates
            and gemini_response_object.candidates[0].content
            and gemini_response_object.candidates[0].content.parts
        ):
            logger.error(
                f"No audio data parts found in the Gemini response for model {Config.MODEL_ID_TTS}."
            )
            return None
        for part in gemini_response_object.candidates[0].content.parts:
            if part.inline_data and part.inline_data.data:
                pcm_data_chunks.append(part.inline_data.data)
            else:
                logger.debug(
                    "Encountered a part without inline_data.data in Gemini response."
                )
        if not pcm_data_chunks:
            logger.warning(
                f"No PCM data was received from Gemini for model {Config.MODEL_ID_TTS}."
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
            ogg_opus_bytes = await self.ffmpeg_wrapper.convert_audio(
                input_data=pcm_data,
                input_format=pcm_format,
                output_format="opus",
                input_args=input_args,
                output_args=output_args,
            )
            if not ogg_opus_bytes:
                logger.error("FFmpeg conversion produced no output.")
                return None
        except Exception as e:
            logger.error(f"FFmpeg conversion failed: {e}.", exc_info=True)
            return None
        duration_secs, waveform_b64 = get_audio_duration_and_waveform(
            ogg_opus_bytes, max_waveform_points=256
        )
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
        if function_name == "generate_speech_ogg":
            text_for_tts = args.get("text_for_tts")
            style = args.get("style")
            if not text_for_tts:
                logger.warning(
                    "Missing 'text_for_tts' argument for generate_speech_ogg."
                )
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
                    logger.error("Failed to generate speech audio.")
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
                logger.error(
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
