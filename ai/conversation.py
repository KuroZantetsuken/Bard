import logging
from datetime import datetime, timezone
from typing import Any, Dict, List

from google.genai import types as gemini_types

from ai.context import ChatHistoryManager, HistoryEntry
from ai.core import GeminiCore
from ai.memory import MemoryManager
from ai.prompts import PromptBuilder
from ai.settings import GeminiConfigManager
from ai.types import FinalAIResponse
from bot.types import ParsedMessageContext
from config import Config
from tools.base import ToolContext
from tools.registry import ToolRegistry
from utilities.logging import prettify_json_for_logging, sanitize_response_for_logging
from utilities.media import MimeDetector

# Initialize logger for the AI conversation module.
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
        memory_manager: MemoryManager,
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
            memory_manager: Manages long-term user memories.
            tool_registry: Manages available tools and their execution.
        """
        self.config = config
        self.core = core
        self.config_manager = config_manager
        self.prompt_builder = prompt_builder
        self.chat_history_manager = chat_history_manager
        self.memory_manager = memory_manager
        self.tool_registry = tool_registry
        self.mime_detector = MimeDetector()

    async def _process_tool_response_part(
        self, tool_response_part: gemini_types.Part, tool_context: ToolContext
    ) -> None:
        """
        Processes a single tool response part to extract and store media data.

        Args:
            tool_response_part: The Gemini types.Part object from a tool's response.
            tool_context: The shared ToolContext to store extracted data.
        """
        # Ensure that tool_response_part is a gemini_types.Part
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
                # This block can be extended for other function response types if needed.
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

        guild_id = parsed_context.discord_context.get("guild_id")
        user_id = parsed_context.discord_context["sender_user_id"]
        is_dm = guild_id is None

        # Reset tool context data at the beginning of each run.
        self.tool_registry.reset_tool_context_data()
        tool_context = self.tool_registry.shared_tool_context
        if tool_context is None:
            raise ValueError("ToolContext not initialized in ToolRegistry.")

        tool_context.guild = parsed_context.guild

        # Load existing chat history for the user/guild.
        history_entries = await self.chat_history_manager.load_history(
            guild_id, str(user_id) if not is_dm else None
        )
        contents_for_gemini = [entry.content for entry in history_entries]

        # Build prompt parts based on the user's message and attachments.
        (
            gemini_prompt_parts,
            is_empty,
        ) = await self.prompt_builder.build_prompt_parts(
            user_id=user_id,
            user_message_content=parsed_context.cleaned_content,
            attachments_data=parsed_context.attachments_data,
            attachments_mime_types=parsed_context.attachments_mime_types,
            processed_image_url_parts=parsed_context.processed_image_url_parts,
            video_urls=parsed_context.video_urls,
            video_metadata_list=parsed_context.video_metadata_list,
            reply_chain_context_text=parsed_context.cleaned_reply_chain_text,
            discord_context=parsed_context.discord_context,
            raw_urls_for_model=parsed_context.raw_urls_for_model,
        )

        # Handle empty prompts or no history.
        if is_empty and not contents_for_gemini:
            return FinalAIResponse(
                text_content="Hello! How can I help you today?",
                media={},
                tool_emojis=[],
                message_id=parsed_context.discord_context.get("message_id"),
            )

        # Append new user content to the last user turn or create a new user turn.
        if contents_for_gemini and contents_for_gemini[-1].role == "user":
            if contents_for_gemini[-1].parts is None:
                contents_for_gemini[-1].parts = []
            contents_for_gemini[-1].parts.extend(gemini_prompt_parts)
        else:
            user_turn_content = gemini_types.Content(
                role="user", parts=gemini_prompt_parts
            )
            contents_for_gemini.append(user_turn_content)

        # Get tool declarations for the Gemini model.
        tool_declarations = self.tool_registry.get_all_function_declarations()

        # Determine system instruction and tools for the model configuration.
        system_instruction_str = None
        if (
            hasattr(self.prompt_builder, "system_prompt")
            and self.prompt_builder.system_prompt
        ):
            system_instruction_str = self.prompt_builder.system_prompt

        tools_for_config = None
        if tool_declarations:
            tools_for_config = tool_declarations

        # Create the Gemini generation configuration.
        main_config = self.config_manager.create_config(
            system_instruction_str=system_instruction_str,
            tool_declarations=tools_for_config,
        )

        generate_content_kwargs: Dict[str, Any] = {
            "config": main_config,
        }

        # Log the request payload before sending to Gemini.
        loggable_request_payload = {
            "system_instruction": system_instruction_str,
            "contents": [c.model_dump() for c in contents_for_gemini],
            "tools": [t.model_dump() for t in main_config.tools or []],
            "generation_config": main_config.model_dump(),
        }
        logger.debug(
            f"REQUEST to Gemini (model: {self.config.MODEL_ID}):\n"
            f"{prettify_json_for_logging(sanitize_response_for_logging(loggable_request_payload))}"
        )

        # Initial content generation call to Gemini.
        response = await self.core.generate_content(
            model=self.config.MODEL_ID,
            contents=contents_for_gemini,
            **generate_content_kwargs,
        )

        # Log the initial response from Gemini.
        loggable_response = response.model_dump()
        logger.debug(
            f"RESPONSE from Gemini (model: {self.config.MODEL_ID}):\n"
            f"{prettify_json_for_logging(sanitize_response_for_logging(loggable_response))}"
        )

        final_text_parts = []
        used_tool_emojis = []

        model_response = response

        # Loop to handle tool calls and subsequent model responses.
        while True:
            if not model_response.candidates:
                break

            model_response_content = model_response.candidates[0].content
            function_calls = [
                part.function_call
                for part in model_response_content.parts
                if hasattr(part, "function_call") and part.function_call
            ]

            # If no function calls, extract text and break the loop.
            if not function_calls:
                final_text_parts.extend(
                    part.text for part in model_response_content.parts if part.text
                )
                contents_for_gemini.append(model_response_content)
                break

            # Append the model's response (with tool calls) to the conversation history.
            contents_for_gemini.append(model_response_content)

            tool_response_parts = []
            # Execute each function call.
            for function_call in function_calls:
                if function_call.name == "generate_speech_ogg":
                    logger.info("TTS tool detected. Preparing for audio output.")

                tool_response_part = await self.tool_registry.execute_function(
                    function_name=function_call.name,
                    args=dict(function_call.args),
                    context=tool_context,
                )

                # Add tool emoji if available.
                tool_class_name = self.tool_registry.function_to_tool_map.get(
                    function_call.name
                )
                if tool_class_name:
                    tool_emoji = self.tool_registry.tool_emojis.get(tool_class_name)
                    if tool_emoji:
                        used_tool_emojis.append(tool_emoji)

                if tool_response_part:
                    tool_response_parts.append(tool_response_part)
                    await self._process_tool_response_part(
                        tool_response_part, tool_context
                    )

            # Append tool response parts to the conversation history.
            for tr_part in tool_response_parts:
                contents_for_gemini.append(gemini_types.Content(parts=[tr_part]))

            # Call Gemini again with the updated conversation history including tool responses.
            response = await self.core.generate_content(
                model=self.config.MODEL_ID,
                contents=contents_for_gemini,
                **generate_content_kwargs,
            )
            model_response = response

        # Build final response data.
        final_text, final_media = self._build_final_response_data(
            tool_context, final_text_parts
        )

        # Append new turns to existing history entries.
        new_history_content = contents_for_gemini[len(history_entries) :]
        for content in new_history_content:
            history_entries.append(
                HistoryEntry(timestamp=datetime.now(timezone.utc), content=content)
            )

        # Save the updated chat history.
        await self.chat_history_manager.save_history(
            guild_id,
            str(user_id) if not is_dm else None,
            history_entries,
        )

        return FinalAIResponse(
            text_content=final_text,
            media=final_media,
            tool_emojis=used_tool_emojis,
            message_id=parsed_context.discord_context.get("message_id"),
        )
