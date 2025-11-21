import logging
import os
from typing import Any, List, Optional, Tuple

from google.genai import types as gemini_types

from ai.chat.files import AttachmentProcessor
from ai.context.dynamic import DynamicContextFormatter
from ai.context.videos import VideoFormatter
from bot.types import DiscordContext, VideoMetadata
from scraper.models import ScrapedData

log = logging.getLogger("Bard")


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
        log.warning(f"Prompt directory not found: {directory}. Using default prompt.")
        return "You are a helpful assistant."

    for filename in sorted(os.listdir(directory)):
        if filename.endswith(".prompt.md"):
            try:
                with open(
                    os.path.join(directory, filename), "r", encoding="utf-8"
                ) as f:
                    prompt_parts.append(f.read())
            except IOError as e:
                log.error(f"Error reading prompt file {filename}: {e}.")

    if not prompt_parts:
        log.warning(
            f"No prompt files found in directory: {directory}. Using default prompt."
        )
        return "You are a helpful assistant."

    log.info(f"Loaded {len(prompt_parts)} prompt files from {directory}.")
    return "\n".join(prompt_parts)


class PromptBuilder:
    """
    Constructs the final prompt payload for the Gemini API,
    incorporating various contextual elements like Discord context, memories, and attachments.
    """

    def __init__(
        self,
        attachment_processor: AttachmentProcessor,
        system_prompt: str,
    ):
        """
        Initializes the PromptBuilder.

        Args:
            attachment_processor: An instance of AttachmentProcessor for handling media.
            system_prompt: The base system instruction for the AI.
        """
        log.debug("Initializing PromptBuilder")
        self.attachment_processor = attachment_processor
        self.system_prompt = system_prompt
        self.dynamic_context_formatter = DynamicContextFormatter()
        self.video_formatter = VideoFormatter()

    async def build_prompt_parts(
        self,
        message_content: str,
        attachments_data: List[bytes],
        attachments_mime_types: List[str],
        video_urls: List[gemini_types.File],
        video_metadata_list: List[VideoMetadata],
        reply_chain_content: Optional[str],
        discord_context: DiscordContext,
        scraped_url_data: List[ScrapedData],
        formatted_memories: Optional[str] = None,
    ) -> Tuple[List[gemini_types.Part], bool]:
        """
        Constructs the prompt parts for the Gemini AI,
        incorporating various contextual elements like Discord context, memories, and attachments.

        Args:
            message_content: The main text content of the user's message.
            attachments_data: Raw byte data of direct attachments.
            attachments_mime_types: MIME types corresponding to attachments_data.
            video_urls: Already processed Gemini parts for video URLs.
            video_metadata_list: Metadata for videos detected in the message.
            reply_chain_content: Text from the Discord reply chain.
            discord_context: Discord-specific context information.
            scraped_url_data: A list of ScrapedData objects.
            formatted_memories: Optional string containing formatted user memories.

        Returns:
            A tuple containing:
            - List[gemini_types.Part]: The list of constructed prompt parts.
            - bool: True if the prompt is effectively empty after processing, False otherwise.
        """
        log.debug(
            "Building prompt parts",
            extra={
                "message_content_len": len(message_content),
                "attachments_count": len(attachments_data),
                "video_urls_count": len(video_urls),
                "video_metadata_count": len(video_metadata_list),
                "reply_chain_content_len": len(reply_chain_content or ""),
                "scraped_url_data_count": len(scraped_url_data),
                "formatted_memories_len": len(formatted_memories or ""),
            },
        )
        prompt_parts: List[Any] = []
        seen_media_identifiers = set()

        if discord_context:
            prompt_parts.append(
                gemini_types.Part(
                    text=self.dynamic_context_formatter.format_discord_context(
                        discord_context
                    )
                )
            )

        if formatted_memories:
            prompt_parts.append(gemini_types.Part(text=formatted_memories))

        if reply_chain_content:
            prompt_parts.append(gemini_types.Part(text=reply_chain_content))

        if message_content.strip():
            prompt_parts.append(gemini_types.Part(text=message_content))

        if scraped_url_data:
            log.debug(f"Processing {len(scraped_url_data)} scraped URL data objects.")
            for i, scraped_data in enumerate(scraped_url_data):
                if not scraped_data:
                    log.warning(f"Scraped data object at index {i} is None. Skipping.")
                    continue

                if scraped_data.text_content:
                    prompt_parts.append(
                        gemini_types.Part(
                            text=f"[SCRAPED CONTENT: {scraped_data.url.resolved}]\n{scraped_data.text_content}\n[/SCRAPED CONTENT]"
                        )
                    )
                else:
                    prompt_parts.append(
                        gemini_types.Part(
                            text=f"[SCRAPED URL: {scraped_data.url.resolved}]\n(No text content was extracted.)\n[/SCRAPED URL]"
                        )
                    )

                if scraped_data.screenshot_data:
                    prompt_parts.append(
                        gemini_types.Part(
                            inline_data=gemini_types.Blob(
                                mime_type="image/png", data=scraped_data.screenshot_data
                            )
                        )
                    )

        for i, data in enumerate(attachments_data):
            mime_type = attachments_mime_types[i]
            log.debug(f"Processing attachment {i} with MIME type: {mime_type}")

            if mime_type.startswith("text/") or mime_type == "application/json":
                try:
                    decoded_text = data.decode("utf-8")
                    prompt_parts.append(
                        gemini_types.Part(
                            text=f"ATTACHMENT_START (MIME: {mime_type})\n```\n{decoded_text}\n```\nATTACHMENT_END"
                        )
                    )
                    log.debug(f"Included text attachment {i} as text part.")
                    continue
                except UnicodeDecodeError:
                    log.warning(
                        f"Could not decode text attachment {i} with MIME type {mime_type}. Attempting as file_data."
                    )
                except Exception as e:
                    log.error(
                        f"Error processing text attachment {i}: {e}. Attempting as file_data."
                    )

            uploaded_file = await self.attachment_processor.upload_media_bytes(
                data, f"attachment_{i}", mime_type
            )
            if uploaded_file:
                if isinstance(uploaded_file, gemini_types.Part):
                    if uploaded_file.inline_data:
                        log.debug(
                            f"Attachment {i} upload failed, using inline data fallback."
                        )
                    else:
                        log.warning(
                            f"Attachment {i} upload failed, adding error message to prompt."
                        )
                    prompt_parts.append(uploaded_file)
                    continue

                identifier = uploaded_file.uri
                if identifier and identifier not in seen_media_identifiers:
                    prompt_parts.append(
                        gemini_types.Part(
                            file_data=gemini_types.FileData(
                                mime_type=uploaded_file.mime_type,
                                file_uri=uploaded_file.uri,
                            )
                        )
                    )
                    seen_media_identifiers.add(identifier)
                elif identifier:
                    log.debug(
                        f"Skipping duplicate direct attachment {i} with identifier: {identifier}."
                    )

        for video_file in video_urls:
            if video_file and hasattr(video_file, "uri"):
                identifier = video_file.uri
                if identifier not in seen_media_identifiers:
                    prompt_parts.append(
                        gemini_types.Part(
                            file_data=gemini_types.FileData(
                                mime_type=video_file.mime_type,
                                file_uri=video_file.uri,
                            )
                        )
                    )
                    seen_media_identifiers.add(identifier)
                else:
                    log.debug(
                        f"Skipping duplicate video file with identifier: {identifier}."
                    )
            elif video_file:
                log.debug(f"Video file object has no uri: {video_file}. Skipping.")

        for video_metadata in video_metadata_list:
            if video_metadata:
                metadata_text_part = gemini_types.Part(
                    text=self.video_formatter.format_video_metadata(video_metadata)
                )

                identifier = f"metadata_{video_metadata.url}"
                if identifier not in seen_media_identifiers:
                    prompt_parts.append(metadata_text_part)
                    seen_media_identifiers.add(identifier)
                else:
                    log.debug(
                        f"Skipping duplicate video metadata part with identifier: {identifier}."
                    )

        is_empty = not any(
            [
                message_content.strip(),
                prompt_parts,
            ]
        )

        log.debug(
            "Finished building prompt parts",
            extra={"prompt_parts_count": len(prompt_parts), "is_empty": is_empty},
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
