import logging
import re
from datetime import datetime, timezone
from typing import List, Optional, Set

import discord
from discord import Message
from google.genai import types as gemini_types

from bard.ai.context.replies import ReplyChainConstructor
from bard.ai.files import AttachmentProcessor
from bard.bot.types import DiscordContext, ParsedMessageContext, VideoMetadata

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
        bot_user_id: Optional[int] = None,
    ):
        """
        Initializes the MessageParser.

        Args:
            attachment_processor: An instance of AttachmentProcessor for handling media.
            bot_user_id: The Discord user ID of the bot.
        """
        self.attachment_processor = attachment_processor
        self.bot_user_id = bot_user_id
        self.reply_chain_constructor = ReplyChainConstructor()

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

    def _clean_content_from_urls(self, content: str, urls_to_remove: Set[str]) -> str:
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
            replied_attachments_data,
            replied_attachments_mime_types,
        ) = await self.reply_chain_constructor.build_reply_chain(message)

        cleaned_content = message.content

        processed_video_parts = []
        video_metadata_list: List[VideoMetadata] = []
        processed_image_url_parts: List[gemini_types.Part] = []
        raw_urls_for_model: List[str] = []

        url_pattern = r"https?://\S+"
        combined_content_for_url_extraction = f"{message.content} {reply_chain_text}"
        urls_in_message = re.findall(url_pattern, combined_content_for_url_extraction)

        urls_to_remove_from_content = set()

        for url in urls_in_message:
            (
                video_part,
                image_part,
                video_metadata,
                remaining_url,
            ) = await self.attachment_processor.check_and_process_url(url)

            if video_part:
                processed_video_parts.append(video_part)
                if video_metadata:
                    video_metadata_list.append(video_metadata)
                urls_to_remove_from_content.add(url)
            elif image_part:
                processed_image_url_parts.append(image_part)
                urls_to_remove_from_content.add(url)
            elif remaining_url:
                raw_urls_for_model.append(remaining_url)

        cleaned_content = self._clean_content_from_urls(
            cleaned_content, urls_to_remove_from_content
        )
        cleaned_reply_chain_text = self._clean_content_from_urls(
            reply_chain_text, urls_to_remove_from_content
        )

        current_attachments_data = []
        current_attachments_mime_types = []
        if message.attachments:
            for attachment in message.attachments:
                mime_type = (
                    attachment.content_type
                    if attachment.content_type
                    else "application/octet-stream"
                )
                current_attachments_data.append(await attachment.read())
                current_attachments_mime_types.append(mime_type)

        combined_attachments_data = current_attachments_data + replied_attachments_data
        combined_attachments_mime_types = (
            current_attachments_mime_types + replied_attachments_mime_types
        )

        discord_context = await self._extract_discord_context(message)

        parsed_message_context = ParsedMessageContext(
            original_message=message,
            cleaned_content=cleaned_content,
            guild=message.guild,
            reply_content=cleaned_reply_chain_text,
            discord_context=discord_context,
            attachments_data=combined_attachments_data,
            attachments_mime_types=combined_attachments_mime_types,
            processed_image_url_parts=processed_image_url_parts,
            video_urls=processed_video_parts,
            video_metadata_list=video_metadata_list,
            cleaned_reply_chain_text=cleaned_reply_chain_text,
            raw_urls_for_model=raw_urls_for_model,
        )
        return parsed_message_context
