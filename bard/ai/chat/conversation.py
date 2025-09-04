import logging
from datetime import datetime, timezone
from typing import Any, Dict, List

from google.genai import types as gemini_types

from bard.ai.config import GeminiConfigManager
from bard.ai.context.history import ChatHistoryManager, HistoryEntry
from bard.ai.context.prompts import PromptBuilder
from bard.ai.core import GeminiCore
from bard.ai.types import FinalAIResponse
from bard.bot.types import ParsedMessageContext
from bard.tools.base import ToolContext
from bard.tools.memory import MemoryManager
from bard.tools.registry import ToolRegistry
from bard.util.logging import clean_dict, prettify_json_for_logging
from bard.util.media.media import MimeDetector
from config import Config

logger = logging.getLogger("Bard")


class AIConversation:
    """
    Manages the complete, stateful, multi-step conversational turn with the Gemini API.
    This includes loading chat history, building prompts, generating responses,
    executing tools, and saving updated history.
    """

    def __init__(
        self,
        config: Config,
        core: GeminiCore,
        config_manager: GeminiConfigManager,
        prompt_builder: PromptBuilder,
        chat_history_manager: ChatHistoryManager,
        tool_registry: ToolRegistry,
    ):
        """
        Initializes the AIConversation with necessary dependencies.

        Args:
            config: Application configuration settings.
            core: Core Gemini API interaction handler.
            config_manager: Manages Gemini generation configuration.
            prompt_builder: Constructs prompts for the Gemini API.
            chat_history_manager: Manages short-term chat history.
            tool_registry: Manages available tools and their execution.
        """
        self.config = config
        self.core = core
        self.config_manager = config_manager
        self.prompt_builder = prompt_builder
        self.chat_history_manager = chat_history_manager
        self.tool_registry = tool_registry
        self.mime_detector = MimeDetector()
        self.memory_manager = MemoryManager(
            memory_dir=Config.MEMORY_DIR, max_memories=Config.MAX_MEMORIES
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

        if not isinstance(tool_response_part, gemini_types.Part):
            logger.warning(
                f"Expected gemini_types.Part, but received {type(tool_response_part)}. Skipping processing."
            )
            return

        if tool_response_part.inline_data:
            mime_type = tool_response_part.inline_data.mime_type
            data = tool_response_part.inline_data.data
            if mime_type and data:
                if mime_type.startswith("image/"):
                    extension = self.mime_detector.get_extension(mime_type)
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
        if parsed_context.discord_context is None:
            logger.error("Missing Discord context in parsed message.")
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

        history_entries = self.chat_history_manager.load_history()
        contents_for_gemini = [entry.content for entry in history_entries]

        memories = await self.memory_manager.load_memories(str(user_id))
        formatted_memories = self.memory_manager.format_memories(str(user_id), memories)

        (
            gemini_prompt_parts,
            is_empty,
        ) = await self.prompt_builder.build_prompt_parts(
            user_message_content=parsed_context.cleaned_content,
            attachments_data=parsed_context.attachments_data,
            attachments_mime_types=parsed_context.attachments_mime_types,
            processed_image_url_parts=parsed_context.processed_image_url_parts,
            video_urls=parsed_context.video_urls,
            video_metadata_list=parsed_context.video_metadata_list,
            reply_chain_context_text=parsed_context.cleaned_reply_chain_text,
            discord_context=parsed_context.discord_context,
            raw_urls_for_model=parsed_context.raw_urls_for_model,
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

        loggable_request_payload = {
            "contents": [c.model_dump() for c in contents_for_gemini],
            "generation_config": main_config.model_dump(),
        }
        cleaned_loggable_payload = clean_dict(loggable_request_payload)
        logger.debug(
            f"REQUEST to Gemini (model: {self.config.MODEL_ID}):\n"
            f"{prettify_json_for_logging(cleaned_loggable_payload)}"
        )

        response = await self.core.generate_content(
            model=self.config.MODEL_ID,
            contents=contents_for_gemini,
            **generate_content_kwargs,
        )

        loggable_response = response.model_dump()
        logger.debug(
            f"RESPONSE from Gemini (model: {self.config.MODEL_ID}):\n"
            f"{prettify_json_for_logging(clean_dict(loggable_response))}"
        )

        final_text_parts = []
        used_tool_emojis = []

        model_response = response
        while True:
            if not model_response.candidates:
                break

            current_model_content = model_response.candidates[0].content

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
                        logger.info("TTS tool detected. Preparing for audio output.")

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
                    model=self.config.MODEL_ID,
                    contents=contents_for_gemini,
                    **generate_content_kwargs,
                )
                model_response = response

        final_text, final_media = self._build_final_response_data(
            tool_context, final_text_parts
        )

        new_history_content = contents_for_gemini[len(history_entries) :]
        for content in new_history_content:
            history_entries.append(
                HistoryEntry(timestamp=datetime.now(timezone.utc), content=content)
            )

        self.chat_history_manager.save_history(history_entries)

        return FinalAIResponse(
            text_content=final_text,
            media=final_media,
            tool_emojis=used_tool_emojis,
            message_id=parsed_context.discord_context.get("message_id"),
        )
