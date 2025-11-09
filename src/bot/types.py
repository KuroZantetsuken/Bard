from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, TypedDict

import discord
from google.genai import types as gemini_types

from scraping.models import ScrapedData


class DiscordContext(TypedDict):
    """
    Represents contextual information from a Discord message.
    This includes details about the channel, sender, replied-to user, and message ID.
    """

    channel_id: int
    channel_name: str
    channel_topic: Optional[str]
    users_in_channel: List[int]
    sender_user_id: int
    replied_user_id: Optional[int]
    current_time_utc: str
    guild_id: Optional[int]
    message_id: int


@dataclass
class VideoMetadata:
    """Represents extracted metadata from a video."""

    url: str
    title: Optional[str] = None
    description: Optional[str] = None
    duration_seconds: Optional[float] = None
    upload_date: Optional[str] = None
    uploader: Optional[str] = None
    view_count: Optional[int] = None
    average_rating: Optional[float] = None
    categories: Optional[list[str]] = None
    tags: Optional[list[str]] = None
    is_youtube: bool = False


@dataclass
class ParsedMessageContext:
    """
    A structured representation of a parsed Discord message and its context.
    This dataclass consolidates all relevant information extracted from a Discord message,
    including attachments and metadata.
    """

    message: discord.Message
    guild: Optional[discord.Guild] = None
    reply_chain_content: Optional[str] = None
    all_media_parts: List[Dict[str, Any]] = field(default_factory=list)
    discord_context: Optional[DiscordContext] = None
    attachments_data: List[bytes] = field(default_factory=list)
    attachments_mime_types: List[str] = field(default_factory=list)
    video_urls: List[gemini_types.File] = field(default_factory=list)
    video_metadata_list: List[VideoMetadata] = field(default_factory=list)
    scraped_url_data: List[ScrapedData] = field(default_factory=list)
