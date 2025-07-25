import logging
import os
from typing import Awaitable, Callable, List, Optional, Tuple

from google.genai import types as gemini_types

from ai.files import AttachmentProcessor
from bot.types import DiscordContext, VideoMetadata

# Initialize logger for the prompt builder module.
logger = logging.getLogger("Bard")
ContextManagerInterface = Callable[[int], Awaitable[Tuple[List[dict], str]]]


def load_prompts_from_directory(directory: str) -> str:
    """
    Loads all .prompt.md files from a specified directory and concatenates their content.
    If the directory is not found or no prompt files are present, a default prompt is returned.

    Args:
        directory: The path to the directory containing prompt files.

    Returns:
        A single string containing the combined content of all prompt files,
        or a default prompt if no files are found or the directory is invalid.
    """
    prompt_parts = []
    if not os.path.isdir(directory):
        logger.warning(
            f"Prompt directory not found: {directory}. Using default prompt."
        )
        return "You are a helpful assistant."

    for filename in sorted(os.listdir(directory)):
        if filename.endswith(".prompt.md"):
            try:
                with open(
                    os.path.join(directory, filename), "r", encoding="utf-8"
                ) as f:
                    prompt_parts.append(f.read())
            except IOError as e:
                logger.error(f"Error reading prompt file {filename}: {e}.")

    if not prompt_parts:
        logger.warning(
            f"No prompt files found in directory: {directory}. Using default prompt."
        )
        return "You are a helpful assistant."

    return "\n".join(prompt_parts)


class PromptBuilder:
    """
    Constructs the final prompt payload for the Gemini API,
    incorporating various contextual elements like Discord context, memories, and attachments.
    """

    def __init__(
        self,
        context_manager: ContextManagerInterface,
        attachment_processor: AttachmentProcessor,
        system_prompt: str,
    ):
        """
        Initializes the PromptBuilder.

        Args:
            context_manager: A callable for managing user context and memories.
            attachment_processor: An instance of AttachmentProcessor for handling media.
            system_prompt: The base system instruction for the AI.
        """
        self.context_manager = context_manager
        self.attachment_processor = attachment_processor
        self.system_prompt = system_prompt

    @staticmethod
    def _format_discord_context(context: DiscordContext) -> str:
        """
        Formats the Discord context dictionary into a readable string for the prompt.

        Args:
            context: A dictionary containing Discord-specific context information.

        Returns:
            A formatted string representing the Discord context.
        """
        formatted_context = [
            "[DYNAMIC_CONTEXT]",
            f"Channel ID: <#{context['channel_id']}>",
            f"Channel Name: {context['channel_name']}",
        ]
        if context["channel_topic"]:
            formatted_context.append(f"Channel Topic: {context['channel_topic']}")

        if context["users_in_channel"]:
            users_formatted = " ".join(
                [f"<@{user_id}>" for user_id in context["users_in_channel"]]
            )
            formatted_context.append(f"Users in Channel: {users_formatted}")

        formatted_context.append(f"Sender User ID: <@{context['sender_user_id']}>")
        formatted_context.append(f"Replied User ID: <@{context['replied_user_id']}>")

        formatted_context.append(f"Current Time (UTC): {context['current_time_utc']}")
        formatted_context.append("[/DYNAMIC_CONTEXT]")
        return "\n".join(formatted_context)

    def _format_video_metadata(self, metadata: VideoMetadata) -> str:
        """
        Formats video metadata into a readable string for the prompt.

        Args:
            metadata: A VideoMetadata object containing details about a video.

        Returns:
            A formatted string representing the video metadata.
        """
        formatted_metadata = ["[VIDEO_METADATA]"]
        if metadata.title:
            formatted_metadata.append(f"Title: {metadata.title}")
        if metadata.description:
            formatted_metadata.append(f"Description: {metadata.description}")
        if metadata.duration_seconds is not None:
            formatted_metadata.append(f"Duration: {metadata.duration_seconds} seconds")
        if metadata.upload_date:
            formatted_metadata.append(f"Upload Date: {metadata.upload_date}")
        if metadata.uploader:
            formatted_metadata.append(f"Uploader: {metadata.uploader}")
        if metadata.view_count is not None:
            formatted_metadata.append(f"View Count: {metadata.view_count}")
        if metadata.average_rating is not None:
            formatted_metadata.append(f"Average Rating: {metadata.average_rating}")
        if metadata.categories:
            formatted_metadata.append(f"Categories: {', '.join(metadata.categories)}")
        if metadata.tags:
            formatted_metadata.append(f"Tags: {', '.join(metadata.tags)}")
        formatted_metadata.append(f"Is YouTube: {metadata.is_youtube}")
        formatted_metadata.append(f"URL: {metadata.url}")
        formatted_metadata.append("[/VIDEO_METADATA]")
        return "\n".join(formatted_metadata)

    async def build_prompt_parts(
        self,
        user_id: int,
        user_message_content: str,
        attachments_data: List[bytes],
        attachments_mime_types: List[str],
        processed_image_url_parts: List[gemini_types.Part],
        video_urls: List[gemini_types.Part],
        video_metadata_list: List[VideoMetadata],
        reply_chain_context_text: Optional[str],
        discord_context: DiscordContext,
        raw_urls_for_model: List[str],
        tool_declarations: Optional[str] = None,
    ) -> Tuple[List[gemini_types.Part], bool]:
        """
        Constructs the prompt parts for the Gemini AI,
        incorporating various inputs and preventing media duplication.

        Args:
            user_id: The ID of the user initiating the conversation.
            user_message_content: The main text content of the user's message.
            attachments_data: Raw byte data of direct attachments.
            attachments_mime_types: MIME types corresponding to attachments_data.
            processed_image_url_parts: Already processed Gemini parts for image URLs.
            video_urls: Already processed Gemini parts for video URLs.
            video_metadata_list: Metadata for videos detected in the message.
            reply_chain_context_text: Text from the Discord reply chain.
            discord_context: Discord-specific context information.
            raw_urls_for_model: Raw URLs extracted from the message for tool use.
            tool_declarations: Optional string containing tool declarations.

        Returns:
            A tuple containing:
            - List[gemini_types.Part]: The list of constructed prompt parts.
            - bool: True if the prompt is effectively empty after processing, False otherwise.
        """
        prompt_parts: List[gemini_types.Part] = []
        seen_media_identifiers = set()  # To prevent duplicate media parts.

        if tool_declarations:
            prompt_parts.append(gemini_types.Part(text=tool_declarations))

        if discord_context:
            prompt_parts.append(
                gemini_types.Part(text=self._format_discord_context(discord_context))
            )

        memories, formatted_memories = await self.context_manager(user_id)
        if formatted_memories:
            prompt_parts.append(gemini_types.Part(text=formatted_memories))

        if reply_chain_context_text:
            prompt_parts.append(gemini_types.Part(text=reply_chain_context_text))

        # Original user message content.
        if user_message_content.strip():
            prompt_parts.append(gemini_types.Part(text=user_message_content))

        # Raw URLs for the model to use with InternetTool.
        if raw_urls_for_model:
            raw_urls_text = "\n".join([f"URL: {url}" for url in raw_urls_for_model])
            prompt_parts.append(
                gemini_types.Part(
                    text=f"[RAW_URLS_FOR_MODEL]\n{raw_urls_text}\n[/RAW_URLS_FOR_MODEL]"
                )
            )

        # Handle direct Discord attachments (raw bytes).
        for i, data in enumerate(attachments_data):
            part = await self.attachment_processor.upload_media_bytes(
                data, f"attachment_{i}", attachments_mime_types[i]
            )
            if part:
                identifier = None
                if part.file_data and part.file_data.file_uri:
                    identifier = part.file_data.file_uri
                elif part.inline_data and part.inline_data.data:
                    identifier = f"inline_data_{part.inline_data.mime_type}_{hash(part.inline_data.data)}"

                if identifier and identifier not in seen_media_identifiers:
                    prompt_parts.append(part)
                    seen_media_identifiers.add(identifier)
                elif identifier:
                    logger.debug(
                        f"Skipping duplicate direct attachment {i} with identifier: {identifier}."
                    )
                else:
                    logger.warning(
                        f"Could not generate a unique identifier for direct attachment {i}. Skipping."
                    )

        # Handle already processed image URL parts.
        for part in processed_image_url_parts:
            if part and part.file_data and part.file_data.file_uri:
                identifier = part.file_data.file_uri
                if identifier not in seen_media_identifiers:
                    prompt_parts.append(part)
                    seen_media_identifiers.add(identifier)
                else:
                    logger.debug(
                        f"Skipping duplicate processed image URL part with identifier: {identifier}."
                    )
            elif part:
                logger.debug(
                    f"Processed image URL part has no file_uri or file_data: {part}. Skipping."
                )

        # Handle video URLs (already processed into parts).
        for part in video_urls:
            if part and part.file_data and part.file_data.file_uri:
                identifier = part.file_data.file_uri
                if identifier not in seen_media_identifiers:
                    prompt_parts.append(part)
                    seen_media_identifiers.add(identifier)
                else:
                    logger.debug(
                        f"Skipping duplicate video URL part with identifier: {identifier}."
                    )
            elif part:
                logger.debug(
                    f"Video URL part has no file_uri or file_data: {part}. Skipping."
                )

        # Handle video metadata (text parts).
        for video_metadata in video_metadata_list:
            if video_metadata:
                metadata_text_part = gemini_types.Part(
                    text=self._format_video_metadata(video_metadata)
                )
                # For metadata, use the URL as identifier.
                identifier = f"metadata_{video_metadata.url}"
                if identifier not in seen_media_identifiers:
                    prompt_parts.append(metadata_text_part)
                    seen_media_identifiers.add(identifier)
                else:
                    logger.debug(
                        f"Skipping duplicate video metadata part with identifier: {identifier}."
                    )

        # Re-evaluate is_empty after de-duplication.
        is_empty = not any(
            [
                user_message_content.strip(),
                prompt_parts,  # Check if any parts remain after de-duplication.
                raw_urls_for_model,
            ]
        )

        return prompt_parts, is_empty

    def get_prompt_text_for_summary(
        self, response: Optional[gemini_types.GenerateContentResponse]
    ) -> str:
        """
        Extracts and formats text content from a Gemini response for logging or summary purposes.

        Args:
            response: The Gemini API response object.

        Returns:
            A string containing the extracted text content, or an empty string if no text is found.
        """
        if not response or not response.candidates:
            return ""
        content = response.candidates[0].content
        if not content or not content.parts:
            return ""
        extracted_text = "\n".join(
            part.text for part in content.parts if part.text
        ).strip()
        return extracted_text
