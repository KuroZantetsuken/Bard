import logging
import re
from datetime import datetime, timezone
from typing import List, Optional

import discord
from discord import Message
from google.genai import types

from bard.ai.context.replies import ReplyChainConstructor
from bard.ai.files import AttachmentProcessor
from bard.bot.types import DiscordContext, ParsedMessageContext, VideoMetadata
from bard.scraping.orchestrator import ScrapingOrchestrator
from bard.util.media.video import VideoProcessor

logger = logging.getLogger("Bard")


class MessageParser:
    """
    Transforms a raw discord.Message object into a clean, structured ParsedMessageContext dataclass.
    This includes extracting Discord context, cleaning message content, processing attachments,
    and handling URLs for media or model consumption.
    """

    def __init__(
        self,
        attachment_processor: AttachmentProcessor,
        scraping_orchestrator: ScrapingOrchestrator,
        bot_user_id: Optional[int] = None,
    ):
        """
        Initializes the MessageParser.

        Args:
            attachment_processor: An instance of AttachmentProcessor for handling media.
            scraping_orchestrator: An instance of ScrapingOrchestrator for web scraping.
            bot_user_id: The Discord user ID of the bot.
        """
        self.attachment_processor = attachment_processor
        self.scraping_orchestrator = scraping_orchestrator
        self.bot_user_id = bot_user_id
        self.reply_chain_constructor = ReplyChainConstructor()
        self.video_processor = VideoProcessor()

    async def _extract_discord_context(self, message: Message) -> DiscordContext:
        """
        Extracts relevant Discord environment information from the message.

        Args:
            message: The Discord message object.

        Returns:
            A DiscordContext TypedDict containing extracted context.
        """
        channel = message.channel
        channel_id = channel.id
        channel_name = getattr(channel, "name", "Direct Message")
        channel_topic = getattr(channel, "topic", None)

        users_in_channel = []
        if isinstance(channel, discord.TextChannel):
            for member in channel.members:
                if not member.bot:
                    users_in_channel.append(member.id)
        elif isinstance(channel, discord.DMChannel) and message.author:
            users_in_channel.append(message.author.id)
            if self.bot_user_id:
                users_in_channel.append(self.bot_user_id)

        replied_user_id = None
        if message.reference and message.reference.resolved:
            if isinstance(message.reference.resolved, discord.Message):
                replied_user_id = message.reference.resolved.author.id

        current_time_utc = datetime.now(timezone.utc).isoformat()
        sender_user_id = message.author.id

        discord_context = DiscordContext(
            channel_id=channel_id,
            channel_name=channel_name,
            channel_topic=channel_topic,
            users_in_channel=users_in_channel,
            sender_user_id=sender_user_id,
            replied_user_id=replied_user_id,
            current_time_utc=current_time_utc,
            guild_id=message.guild.id if message.guild else None,
            message_id=message.id,
        )
        return discord_context

    def _clean_content_from_urls(self, content: str, urls_to_remove: set[str]) -> str:
        """
        Removes specified URLs from the given content string.

        Args:
            content: The original string content.
            urls_to_remove: A set of URLs to remove from the content.

        Returns:
            The content string with specified URLs removed.
        """
        cleaned_content = content
        for url_to_remove in urls_to_remove:
            cleaned_content = cleaned_content.replace(url_to_remove, "").strip()
        return cleaned_content

    async def parse(self, message: Message) -> ParsedMessageContext:
        """
        Parses a raw discord.Message into a structured ParsedMessageContext.

        Args:
            message: The raw Discord message object.

        Returns:
            A ParsedMessageContext object containing all extracted and processed information.
        """
        (
            reply_chain_text,
            replied_attachments,
        ) = await self.reply_chain_constructor.build_reply_chain(message)

        url_pattern = r"https?://\S+"
        combined_content_for_url_extraction = f"{message.content} {reply_chain_text}"
        urls_in_message = set(
            re.findall(url_pattern, combined_content_for_url_extraction)
        )

        scraped_data_list = []
        if urls_in_message:
            logger.info(
                f"Found {len(urls_in_message)} URLs to scrape: {urls_in_message}"
            )
            scraped_data_list = await self.scraping_orchestrator.process_urls(
                list(urls_in_message)
            )
            logger.info(f"Scraping complete. Got {len(scraped_data_list)} results.")

        processed_video_parts: List[types.Part] = []
        video_metadata_list: List[VideoMetadata] = []
        urls_to_remove_from_content: set[str] = set()

        for scraped_data in scraped_data_list:
            if not scraped_data:
                continue

            urls_to_remove_from_content.add(scraped_data.resolved_url)

            if scraped_data.video_details and scraped_data.video_details.is_video:
                if scraped_data.video_details.is_youtube:
                    video_part = types.Part(
                        file_data=types.FileData(file_uri=scraped_data.resolved_url)
                    )
                    processed_video_parts.append(video_part)
                    if scraped_data.video_details.metadata:
                        video_metadata = self.video_processor._create_video_metadata(
                            scraped_data.resolved_url,
                            scraped_data.video_details.metadata,
                        )
                        video_metadata_list.append(video_metadata)
                elif scraped_data.video_details.stream_url:
                    stream_url = scraped_data.video_details.stream_url
                    mime_type = (
                        scraped_data.video_details.metadata.get("http_headers", {}).get(
                            "Content-Type", "video/mp4"
                        )
                        if scraped_data.video_details.metadata
                        else "video/mp4"
                    )
                    video_part = types.Part.from_uri(
                        file_uri=stream_url, mime_type=mime_type
                    )
                    processed_video_parts.append(video_part)

        cleaned_content = self._clean_content_from_urls(
            message.content, urls_to_remove_from_content
        )
        cleaned_reply_chain_text = self._clean_content_from_urls(
            reply_chain_text, urls_to_remove_from_content
        )

        attachments_data = []
        attachments_mime_types = []
        all_attachments = message.attachments + replied_attachments
        for attachment in all_attachments:
            mime_type = (
                attachment.content_type
                if attachment.content_type
                else "application/octet-stream"
            )
            attachments_data.append(await attachment.read())
            attachments_mime_types.append(mime_type)

        discord_context = await self._extract_discord_context(message)

        return ParsedMessageContext(
            original_message=message,
            cleaned_content=cleaned_content,
            guild=message.guild,
            reply_content=cleaned_reply_chain_text,
            discord_context=discord_context,
            attachments_data=attachments_data,
            attachments_mime_types=attachments_mime_types,
            video_urls=processed_video_parts,
            video_metadata_list=video_metadata_list,
            cleaned_reply_chain_text=cleaned_reply_chain_text,
            scraped_url_data=scraped_data_list,
            raw_urls_for_model=[],
        )
