import asyncio
import logging
from dataclasses import dataclass
from typing import Any, Optional, cast

import yt_dlp
from yt_dlp.utils import YoutubeDLError

from config import Config

logger = logging.getLogger("Bard")


@dataclass
class VideoDetails:
    """
    A data structure to hold details about a processed URL, indicating
    whether it is a video and containing relevant metadata.
    """

    is_video: bool = False
    is_youtube: bool = False
    metadata: Optional[dict[str, Any]] = None
    stream_url: Optional[str] = None


class VideoHandler:
    """
    Handles video detection and data extraction from a given URL using yt-dlp.
    """

    async def process_url(self, url: str) -> VideoDetails:
        """
        Processes a URL to determine if it is a video, extracts metadata,
        and finds a streamable URL for non-YouTube videos.

        Args:
            url: The URL to process.

        Returns:
            A VideoDetails object with the results of the processing.
        """
        logger.debug(f"Processing URL for video content: {url}")
        try:
            ydl_opts: dict[str, Any] = {
                "executable": Config.YTDLP_PATH,
                "quiet": True,
                "no_warnings": True,
                "dump_single_json": True,
                "noplaylist": True,
                "ignoreerrors": True,
            }

            with yt_dlp.YoutubeDL(ydl_opts) as ydl:  # type: ignore
                metadata_raw = await asyncio.to_thread(
                    ydl.extract_info, url, download=False
                )

            if not metadata_raw:
                logger.info(f"URL is not a video: {url}")
                return VideoDetails(is_video=False)

            metadata = cast(dict[str, Any], metadata_raw)
            is_youtube = metadata.get("extractor_key", "").lower() == "youtube"
            stream_url = None

            if not is_youtube:
                stream_url = await self._get_best_stream_url(url)
                if not stream_url:
                    logger.warning(
                        f"Could not find a streamable URL for non-YouTube video: {url}"
                    )

            logger.info(
                f"Successfully processed video URL: {url} (YouTube: {is_youtube})"
            )
            return VideoDetails(
                is_video=True,
                is_youtube=is_youtube,
                metadata=metadata,
                stream_url=stream_url,
            )

        except YoutubeDLError as e:
            if "Unsupported URL" in str(e) or "No media found" in str(e):
                logger.info(f"URL is not a video or unsupported: {url}")
            else:
                logger.debug(f"yt-dlp could not extract info from {url}: {e}")
            return VideoDetails(is_video=False)
        except Exception:
            logger.error(
                f"An unexpected error occurred while processing URL: {url}",
                exc_info=True,
            )
            return VideoDetails(is_video=False)

    async def _get_best_stream_url(self, url: str) -> Optional[str]:
        """
        Gets the best available streamable URL for a non-YouTube video.
        """
        logger.debug(f"Attempting to find stream URL for: {url}")
        try:
            ydl_opts: dict[str, Any] = {
                "executable": Config.YTDLP_PATH,
                "quiet": True,
                "no_warnings": True,
                "format": "best",
                "get_url": True,
            }
            with yt_dlp.YoutubeDL(ydl_opts) as ydl:  # type: ignore
                info = await asyncio.to_thread(ydl.extract_info, url, download=False)
                return info.get("url") if info else None
        except Exception:
            logger.error(f"Failed to extract stream URL for: {url}", exc_info=True)
            return None
