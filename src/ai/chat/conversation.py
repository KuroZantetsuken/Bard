import logging
import mimetypes
import re
from typing import Any, Dict, List

from google.genai import types as gemini_types

from ai.config import GeminiConfigManager
from ai.context.prompts import PromptBuilder
from ai.core import GeminiCore
from ai.tools.base import ToolContext
from ai.tools.memory import MemoryManager
from ai.tools.registry import ToolRegistry
from ai.types import FinalAIResponse
from bot.types import ParsedMessageContext
from scraping.orchestrator import ScrapingOrchestrator
from settings import Settings

log = logging.getLogger("Bard")


def _parse_urls_from_markdown(markdown_text: str) -> List[str]:
    """Extracts URLs from a markdown string."""

    return re.findall(r"\[.*?\]\(<(https?://[^\s>]+)>", markdown_text)


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
                    tool_context.tool_response_data["image_data"] = data
                    tool_context.tool_response_data["image_filename"] = filename

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
        if tool_context.tool_response_data:
            if tool_context.tool_response_data.get("image_data"):
                final_media["image_data"] = tool_context.tool_response_data[
                    "image_data"
                ]
                final_media["image_filename"] = tool_context.tool_response_data.get(
                    "image_filename"
                )
            if tool_context.tool_response_data.get("code_data"):
                final_media["code_data"] = tool_context.tool_response_data["code_data"]
                final_media["code_filename"] = tool_context.tool_response_data.get(
                    "code_filename"
                )
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

        if "audio_data" in final_media:
            final_text = final_text.strip()
        else:
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

    async def run(self, parsed_context: ParsedMessageContext) -> FinalAIResponse:
        """
        Executes a full AI conversational turn.

        Args:
            parsed_context: The parsed input message context, including Discord-specific details.

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

        contents_for_gemini: List[gemini_types.Content] = []

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

        if is_empty and not contents_for_gemini:
            return FinalAIResponse(
                text_content="Hello! How can I help you today?",
                media={},
                tool_emojis=[],
                message_id=parsed_context.discord_context.get("message_id"),
            )

        if contents_for_gemini and contents_for_gemini[-1].role == "user":
            if contents_for_gemini[-1].parts is None:
                contents_for_gemini[-1].parts = []
            contents_for_gemini[-1].parts.extend(gemini_prompt_parts)
        else:
            user_turn_content = gemini_types.Content(
                role="user", parts=gemini_prompt_parts
            )
            contents_for_gemini.append(user_turn_content)

        tool_declarations = self.tool_registry.get_all_function_declarations()

        system_instruction_str = None
        if (
            hasattr(self.prompt_builder, "system_prompt")
            and self.prompt_builder.system_prompt
        ):
            system_instruction_str = self.prompt_builder.system_prompt

        tools_for_config = None
        if tool_declarations:
            tools_for_config = tool_declarations

        main_config = self.config_manager.create_config(
            system_instruction_str=system_instruction_str,
            tool_declarations=tools_for_config,
        )

        generate_content_kwargs: Dict[str, Any] = {
            "config": main_config,
        }

        log.debug(
            f"REQUEST to Gemini (model: {self.settings.MODEL_ID})",
            extra={
                "contents": [c.model_dump() for c in contents_for_gemini],
                "generation_config": main_config.model_dump(),
            },
        )

        response = await self.core.generate_content(
            model=self.settings.MODEL_ID,
            contents=contents_for_gemini,
            **generate_content_kwargs,
        )

        log.debug(
            f"RESPONSE from Gemini (model: {self.settings.MODEL_ID})",
            extra={"response": response.model_dump()},
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

            candidate = model_response.candidates
            current_model_content = candidate[0].content

            if current_model_content is None:
                log.warning(
                    f"Model response content is empty. Finish reason: {candidate.finish_reason}. "
                    f"Safety ratings: {candidate.safety_ratings}"
                )
                final_text_parts.append(
                    "My response was blocked. I am unable to provide the requested information."
                )
                break

            current_model_text_parts = [
                p for p in current_model_content.parts if p.text
            ]
            current_model_function_call_parts = [
                p for p in current_model_content.parts if p.function_call
            ]

            if not current_model_function_call_parts:
                final_text_parts.extend(p.text for p in current_model_text_parts)
                contents_for_gemini.append(current_model_content)
                break
            else:
                contents_for_gemini.append(
                    gemini_types.Content(
                        role="model", parts=current_model_function_call_parts
                    )
                )

                tool_response_parts = []
                for function_call_part in current_model_function_call_parts:
                    function_call = function_call_part.function_call
                    if function_call.name == "generate_speech_ogg":
                        log.info("TTS tool detected. Preparing for audio output.")

                    tool_result_part = await self.tool_registry.execute_function(
                        function_name=function_call.name,
                        args=dict(function_call.args),
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

                for tr_part in tool_response_parts:
                    contents_for_gemini.append(
                        gemini_types.Content(role="function", parts=[tr_part])
                    )

                if current_model_text_parts:
                    model_text_content = gemini_types.Content(
                        role="model", parts=current_model_text_parts
                    )
                    contents_for_gemini.append(model_text_content)
                    final_text_parts.extend(p.text for p in current_model_text_parts)

                response = await self.core.generate_content(
                    model=self.settings.MODEL_ID,
                    contents=contents_for_gemini,
                    **generate_content_kwargs,
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
