import asyncio
import logging
from typing import Any, Optional

import yt_dlp

from bard.bot.types import VideoMetadata
from bard.util.media.ffmpeg import FFmpegWrapper
from config import Config

logger = logging.getLogger("Bard")


class VideoProcessor:
    """
    Handles video processing using yt-dlp for information extraction and stream URL retrieval,
    and FFmpeg for streaming media content.
    """

    @staticmethod
    async def get_video_info(url: str) -> Optional[dict[str, Any]]:
        """
        Extracts video information from a given URL using yt-dlp.

        Args:
            url: The URL of the video.

        Returns:
            An optional dictionary containing video information, or None if extraction fails.
        """
        ydl_opts: dict[str, Any] = {
            "executable": Config.YTDLP_PATH,
            "force_generic_extractor": True,
            "noplaylist": True,
            "ignoreerrors": True,
            "no_warnings": True,
            "dump_single_json": True,
            "quiet": True,
        }
        try:
            with yt_dlp.YoutubeDL(ydl_opts) as ydl:  # type: ignore
                return await asyncio.to_thread(ydl.extract_info, url, download=False)  # type: ignore
        except Exception as e:
            logger.error(f"Error extracting video info for {url}: {e}", exc_info=True)
            return None

    @staticmethod
    def _create_video_metadata(url: str, info_dict: dict[str, Any]) -> VideoMetadata:
        """
        Creates a VideoMetadata object from a yt-dlp info dictionary.

        Args:
            url: The original URL of the video.
            info_dict: The dictionary returned by yt-dlp's `extract_info`.

        Returns:
            A VideoMetadata object populated with extracted information.
        """
        is_youtube = "youtube.com" in url or "youtu.be" in url
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
            is_youtube=is_youtube,
        )

    @staticmethod
    async def get_stream_url(url: str, format_selector: str) -> Optional[str]:
        """
        Retrieves a streamable URL for a given video format using yt-dlp.

        Args:
            url: The URL of the video.
            format_selector: The yt-dlp format selector string (e.g., "bestvideo+bestaudio").

        Returns:
            An optional string containing the streamable URL, or None if retrieval fails.
        """
        ydl_opts: dict[str, Any] = {
            "executable": Config.YTDLP_PATH,
            "format": format_selector,
            "get_url": True,
            "noplaylist": True,
            "ignoreerrors": True,
            "no_warnings": True,
            "quiet": True,
        }
        try:
            with yt_dlp.YoutubeDL(ydl_opts) as ydl:  # type: ignore
                info: dict[str, Any] | None = await asyncio.to_thread(
                    ydl.extract_info, url, download=False
                )  # type: ignore
                return info.get("url") if info else None
        except Exception as e:
            logger.error(f"Error getting stream URL for {url}: {e}", exc_info=True)
            return None

    @staticmethod
    async def stream_media(stream_url: str, ffmpeg_args: list[str]) -> Optional[bytes]:
        """
        Streams media content from a URL using FFmpeg and captures its output.

        Args:
            stream_url: The URL of the media stream.
            ffmpeg_args: A list of FFmpeg arguments to apply during streaming.

        Returns:
            The streamed media content in bytes, or None on failure or timeout.
        """
        command = [Config.FFMPEG_PATH, "-i", stream_url] + ffmpeg_args
        stdout, stderr, return_code = await FFmpegWrapper.execute(
            command, timeout=Config.TOOL_TIMEOUT_SECONDS
        )

        if return_code == 0 and stdout:
            return stdout
        else:
            error_msg = stderr.decode(errors="ignore") if stderr else "Unknown error"
            logger.error(
                f"FFmpeg streaming failed for {stream_url} with code {return_code}: {error_msg}"
            )
            return None
