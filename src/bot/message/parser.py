import logging
import mimetypes
import re
from datetime import datetime, timezone
from typing import Any, List, Optional

import discord
from discord import Message
from google.genai import types

from ai.chat.files import AttachmentProcessor
from ai.context.replies import ReplyChainConstructor
from bot.types import DiscordContext, ParsedMessageContext, VideoMetadata
from scraper.orchestrator import ScrapingOrchestrator

log = logging.getLogger("Bard")


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
        log.debug("MessageParser initialized.")

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
        log.debug("Extracted Discord context.", extra={"context": {**discord_context}})
        return discord_context

    def _create_video_metadata(
        self, url: str, info_dict: dict[str, Any]
    ) -> VideoMetadata:
        """
        Creates a VideoMetadata object from a yt-dlp info dictionary.

        Args:
            url: The original URL of the video.
            info_dict: The dictionary returned by yt-dlp's `extract_info`.

        Returns:
            A VideoMetadata object populated with extracted information.
        """
        return VideoMetadata(
            url=url,
            title=info_dict.get("title"),
            description=info_dict.get("description"),
            duration_seconds=info_dict.get("duration"),
            upload_date=info_dict.get("upload_date"),
            uploader=info_dict.get("uploader"),
            view_count=info_dict.get("view_count"),
            average_rating=info_dict.get("average_rating"),
            categories=info_dict.get("categories"),
            tags=info_dict.get("tags"),
            is_youtube="youtube.com" in url or "youtu.be" in url,
        )

    async def parse(self, message: Message) -> ParsedMessageContext:
        """
        Parses a raw discord.Message into a structured ParsedMessageContext.

        Args:
            message: The raw Discord message object.

        Returns:
            A ParsedMessageContext object containing all extracted and processed information.
        """
        log.debug("Building reply chain.", extra={"message_id": message.id})
        (
            reply_chain_text,
            replied_attachments,
        ) = await self.reply_chain_constructor.build_reply_chain(message)
        log.debug(
            "Reply chain built.",
            extra={
                "message_id": message.id,
                "chain_length": len(reply_chain_text),
                "attachment_count": len(replied_attachments),
            },
        )

        url_pattern = r"(?:https?://|www\.)[a-zA-Z0-9\-\._~:/?#\[\]@!$&'()*+,;=%]+"
        combined_content_for_url_extraction = f"{message.content} {reply_chain_text}"
        urls_in_message = set(
            re.findall(url_pattern, combined_content_for_url_extraction)
        )

        scraped_data_list = []
        if urls_in_message:
            log.info(
                "Found URLs to scrape.",
                extra={"count": len(urls_in_message), "urls": list(urls_in_message)},
            )
            scraped_data_list = await self.scraping_orchestrator.process_urls(
                list(urls_in_message)
            )
            log.debug(
                "Scraping complete.",
                extra={"results_count": len(scraped_data_list)},
            )

        processed_video_parts: List[types.File] = []
        video_metadata_list: List[VideoMetadata] = []
        for scraped_data in scraped_data_list:
            if not scraped_data:
                continue

            if scraped_data.video_details and scraped_data.video_details.is_video:
                log.debug(
                    "Processing scraped video data.",
                    extra={"url": scraped_data.url.resolved},
                )
                if scraped_data.video_details.video_path:
                    mime_type, _ = mimetypes.guess_type(
                        scraped_data.video_details.video_path
                    )
                    if mime_type:
                        try:
                            with open(scraped_data.video_details.video_path, "rb") as f:
                                video_bytes = f.read()
                            video_file = (
                                await self.attachment_processor.upload_media_bytes(
                                    video_bytes,
                                    display_name=scraped_data.video_details.video_path,
                                    mime_type=mime_type,
                                )
                            )
                            if video_file:
                                processed_video_parts.append(video_file)
                                log.debug(
                                    "Uploaded video file from scraped data.",
                                    extra={"uri": video_file.uri},
                                )
                        except Exception:
                            log.error(
                                f"Failed to read and upload video file: {scraped_data.video_details.video_path}",
                                exc_info=True,
                            )

                if scraped_data.video_details.metadata:
                    video_metadata = self._create_video_metadata(
                        scraped_data.url.resolved,
                        scraped_data.video_details.metadata,
                    )
                    video_metadata_list.append(video_metadata)
                    log.debug(
                        "Created video metadata from scraped data.",
                        extra={"url": scraped_data.url.resolved},
                    )

        attachments_data = []
        attachments_mime_types = []
        all_attachments = message.attachments + replied_attachments
        log.debug(
            "Processing attachments.",
            extra={"attachment_count": len(all_attachments)},
        )
        for attachment in all_attachments:
            mime_type = (
                attachment.content_type
                if attachment.content_type
                else "application/octet-stream"
            )
            attachments_data.append(await attachment.read())
            attachments_mime_types.append(mime_type)
        log.debug(
            "Finished processing attachments.",
            extra={"processed_count": len(attachments_data)},
        )

        discord_context = await self._extract_discord_context(message)

        return ParsedMessageContext(
            message=message,
            guild=message.guild,
            reply_chain_content=reply_chain_text,
            discord_context=discord_context,
            attachments_data=attachments_data,
            attachments_mime_types=attachments_mime_types,
            video_urls=processed_video_parts,
            video_metadata_list=video_metadata_list,
            scraped_url_data=scraped_data_list,
        )
