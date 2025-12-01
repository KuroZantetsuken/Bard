import asyncio
import logging
import mimetypes
from typing import Any, Dict, List

from google.genai import errors as genai_errors
from google.genai import types as gemini_types
from google.genai.chats import Chat

from ai.config import GeminiConfigManager
from ai.context.prompts import PromptBuilder
from ai.core import GeminiCore
from ai.tools.base import ToolContext
from ai.tools.memory import MemoryManager
from ai.tools.registry import ToolRegistry
from ai.types import FinalAIResponse
from bot.types import ParsedMessageContext
from scraper.orchestrator import ScrapingOrchestrator
from settings import Settings

log = logging.getLogger("Bard")


class AIConversation:
    """
    Manages the complete, stateful, multi-step conversational turn with the Gemini API.
    This includes building prompts, generating responses,
    executing tools.
    """

    def __init__(
        self,
        settings: Settings,
        core: GeminiCore,
        config_manager: GeminiConfigManager,
        prompt_builder: PromptBuilder,
        tool_registry: ToolRegistry,
        scraping_orchestrator: ScrapingOrchestrator,
    ):
        """
        Initializes the AIConversation with necessary dependencies.

        Args:
            settings: Application configuration settings.
            core: Core Gemini API interaction handler.
            config_manager: Manages Gemini generation configuration.
            prompt_builder: Constructs prompts for the Gemini API.
            tool_registry: Manages available tools and their execution.
            scraping_orchestrator: Manages scraping and caching of URLs.
        """
        log.debug("Initializing AIConversation")
        self.settings = settings
        self.core = core
        self.config_manager = config_manager
        self.prompt_builder = prompt_builder
        self.tool_registry = tool_registry
        self.scraping_orchestrator = scraping_orchestrator
        self.memory_manager = MemoryManager(
            memory_dir=Settings.MEMORY_DIR, max_memories=Settings.MAX_MEMORIES
        )

    async def _process_tool_response_part(
        self, tool_response_part: gemini_types.Part, tool_context: ToolContext
    ) -> None:
        """
        Processes a single tool response part to extract and store media data.

        Args:
            tool_response_part: The Gemini types.Part object from a tool's response.
            tool_context: The shared ToolContext to store extracted data.
        """

        log.debug(
            "Processing tool response part",
            extra={"tool_response_part": tool_response_part},
        )
        if not isinstance(tool_response_part, gemini_types.Part):
            log.warning(
                f"Expected gemini_types.Part, but received {type(tool_response_part)}. Skipping processing."
            )
            return

        if tool_response_part.inline_data:
            mime_type = tool_response_part.inline_data.mime_type
            data = tool_response_part.inline_data.data
            if mime_type and data:
                if mime_type.startswith("image/"):
                    extension = mimetypes.guess_extension(mime_type) or ".bin"
                    filename = f"plot{extension}"
                    tool_context.images.append(
                        {"data": data, "filename": filename, "mime_type": mime_type}
                    )

        if tool_response_part.function_response and isinstance(
            tool_response_part.function_response.response, dict
        ):
            function_response_data = tool_response_part.function_response.response
            if (
                function_response_data.get("success")
                and "duration_secs" in function_response_data
            ):
                pass

    def _build_final_response_data(
        self, tool_context: ToolContext, final_text_parts: List[str]
    ) -> tuple[str, Dict[str, Any]]:
        """
        Builds the final text content and media dictionary for the AI response.

        Args:
            tool_context: The shared ToolContext containing extracted data.
            final_text_parts: A list of text parts extracted from the model's response.

        Returns:
            A tuple containing the final text content and the media dictionary.
        """
        log.debug(
            "Building final response data",
            extra={
                "tool_context": tool_context,
                "final_text_parts": final_text_parts,
            },
        )
        final_media = {}
        if (
            tool_context.tool_response_data
            or tool_context.images
            or tool_context.code_files
        ):
            if tool_context.images:
                final_media["images"] = tool_context.images

            if tool_context.code_files:
                final_media["code_files"] = tool_context.code_files

            if tool_context.tool_response_data.get("audio_bytes"):
                final_media["audio_data"] = tool_context.tool_response_data[
                    "audio_bytes"
                ]
                final_media["duration_secs"] = tool_context.tool_response_data.get(
                    "duration_secs", 0.0
                )
                final_media["waveform_b64"] = tool_context.tool_response_data.get(
                    "waveform_b64"
                )

        final_text = "\n".join(final_text_parts).strip()

        if not final_media:
            final_text = (
                final_text.strip()
                or "I processed your request but have nothing to add."
            )

        if tool_context and hasattr(tool_context, "grounding_sources_md"):
            grounding_sources = (
                tool_context.grounding_sources_md.strip()
                if tool_context.grounding_sources_md
                else ""
            )
            if grounding_sources:
                final_text += f"\n\n{grounding_sources}"

        log.debug(
            "Finished building final response data",
            extra={"final_text": final_text, "final_media": final_media},
        )
        return final_text, final_media

    async def run(
        self, parsed_context: ParsedMessageContext, chat: Chat
    ) -> FinalAIResponse:
        """
        Executes a full AI conversational turn using a stateful Chat object.

        Args:
            parsed_context: The parsed input message context.
            chat: The stateful Chat object for the current conversation.

        Returns:
            A FinalAIResponse object containing the AI's generated text, media,
            used tool emojis, and the Discord message ID.
        """
        log.debug("Running AIConversation", extra={"parsed_context": parsed_context})
        if parsed_context.discord_context is None:
            log.error("Missing Discord context in parsed message.")
            return FinalAIResponse(
                text_content="An internal error occurred: Missing Discord context.",
                media={},
                tool_emojis=[],
                message_id=None,
            )

        user_id = parsed_context.discord_context["sender_user_id"]

        self.tool_registry.reset_tool_context_data()
        tool_context = self.tool_registry.shared_tool_context
        if tool_context is None:
            raise ValueError("ToolContext not initialized in ToolRegistry.")

        tool_context.guild = parsed_context.guild
        tool_context.user_id = str(user_id)
        tool_context.channel = parsed_context.message.channel

        memories = await self.memory_manager.load_memories(str(user_id))
        formatted_memories = self.memory_manager.format_memories(str(user_id), memories)

        (
            gemini_prompt_parts,
            is_empty,
        ) = await self.prompt_builder.build_prompt_parts(
            message_content=parsed_context.message.content,
            attachments_data=parsed_context.attachments_data,
            attachments_mime_types=parsed_context.attachments_mime_types,
            video_urls=parsed_context.video_urls,
            video_metadata_list=parsed_context.video_metadata_list,
            reply_chain_content=parsed_context.reply_chain_content,
            discord_context=parsed_context.discord_context,
            scraped_url_data=parsed_context.scraped_url_data,
            formatted_memories=formatted_memories,
        )

        if is_empty:
            return FinalAIResponse(
                text_content="Hello! How can I help you today?",
                media={},
                tool_emojis=[],
                message_id=parsed_context.discord_context.get("message_id"),
            )

        log.debug(
            f"REQUEST to Gemini (model: {self.settings.MODEL_ID})",
            extra={
                "parts_count": len(gemini_prompt_parts),
            },
        )

        response = None
        retries = 3
        delay = 2
        for attempt in range(retries):
            try:
                response = await asyncio.to_thread(
                    chat.send_message, gemini_prompt_parts
                )
                break
            except genai_errors.ServerError as e:
                if "503" in str(e) and attempt < retries - 1:
                    log.warning(
                        f"Model overloaded (503). Retrying in {delay} seconds... (Attempt {attempt + 1}/{retries})",
                        extra={"error": str(e)},
                    )
                    await asyncio.sleep(delay)
                    delay *= 2
                else:
                    log.error(
                        f"Final attempt failed or non-retryable server error: {e}"
                    )
                    raise

        if not response:
            log.error("AI conversation failed after multiple retries.")
            return FinalAIResponse(
                text_content="The AI model is currently overloaded. Please try again later.",
                media={},
                tool_emojis=[],
                message_id=parsed_context.discord_context.get("message_id"),
            )

        response_text_for_log = ""
        if (
            response.candidates
            and response.candidates[0].content
            and response.candidates[0].content.parts
        ):
            response_text_for_log = "".join(
                p.text for p in response.candidates[0].content.parts if p.text
            )
        log.debug(
            f"RESPONSE from Gemini (model: {self.settings.MODEL_ID})",
            extra={"response_text": response_text_for_log},
        )

        final_text_parts = []
        used_tool_emojis = []

        model_response = response
        while True:
            if not model_response.candidates:
                log.warning(
                    f"Model response has no candidates. Prompt feedback: {response.prompt_feedback}"
                )
                final_text_parts.append(
                    "My response was blocked. I am unable to provide the requested information."
                )
                break

            candidate = model_response.candidates[0]
            current_model_content = candidate.content

            if current_model_content is None:
                log.warning(
                    f"Model response content is empty. Finish reason: {candidate.finish_reason}. "
                    f"Safety ratings: {candidate.safety_ratings}"
                )
                final_text_parts.append(
                    "My response was blocked. I am unable to provide the requested information."
                )
                break

            parts = current_model_content.parts or []
            current_model_text_parts = [p for p in parts if p.text]
            current_model_function_call_parts = [p for p in parts if p.function_call]

            if not current_model_function_call_parts:
                final_text_parts.extend(p.text for p in current_model_text_parts)
                break
            else:
                tool_response_parts = []
                for function_call_part in current_model_function_call_parts:
                    function_call = function_call_part.function_call
                    if not function_call or not function_call.name:
                        continue

                    if function_call.name == "generate_speech_ogg":
                        log.info("TTS tool detected. Preparing for audio output.")

                    args_for_tool = {}
                    if function_call.args:
                        args_for_tool = {k: v for k, v in function_call.args.items()}

                    tool_result_part = await self.tool_registry.execute_function(
                        function_name=function_call.name,
                        args=args_for_tool,
                        context=tool_context,
                    )

                    tool_class_name = self.tool_registry.function_to_tool_map.get(
                        function_call.name
                    )
                    if tool_class_name:
                        tool_emoji = self.tool_registry.tool_emojis.get(tool_class_name)
                        if tool_emoji:
                            used_tool_emojis.append(tool_emoji)

                    if tool_result_part:
                        tool_response_parts.append(tool_result_part)
                        await self._process_tool_response_part(
                            tool_result_part, tool_context
                        )

                if current_model_text_parts:
                    final_text_parts.extend(p.text for p in current_model_text_parts)

                response = await asyncio.to_thread(
                    chat.send_message, tool_response_parts
                )
                model_response = response

        final_text, final_media = self._build_final_response_data(
            tool_context, final_text_parts
        )

        final_response = FinalAIResponse(
            text_content=final_text,
            media=final_media,
            tool_emojis=used_tool_emojis,
            message_id=parsed_context.discord_context.get("message_id"),
        )
        log.debug("Finished running AIConversation", extra={"response": final_response})
        return final_response
