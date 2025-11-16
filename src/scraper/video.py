import asyncio
import logging
from pathlib import Path
from typing import Any, Optional, cast

import yt_dlp
from yt_dlp.utils import YoutubeDLError, match_filter_func

from scraper.cache import CacheManager
from scraper.models import ResolvedURL, VideoDetails
from settings import Settings

log = logging.getLogger("Bard")


class VideoHandler:
    def __init__(self, cache_manager: CacheManager):
        """
        Initializes the VideoHandler.
        Args:
            cache_manager: An instance of CacheManager for handling video cache.
        """
        self.cache_manager = cache_manager

    async def process_url(self, url_obj: ResolvedURL) -> VideoDetails:
        """
        Processes a URL to determine if it is a video, extracts metadata, and downloads the video.
        Args:
            url_obj: The ResolvedURL to process.
        Returns:
            A VideoDetails object with the results of the processing.
        """
        log.debug("Processing URL for video content.", extra={"url": url_obj.resolved})
        resolved_url = url_obj.resolved
        try:
            video_path = self.cache_manager.get_video_path(resolved_url)
            if video_path and video_path.exists():
                log.info(
                    "Video found in cache.",
                    extra={"url": resolved_url, "path": str(video_path)},
                )

                metadata = await self._extract_metadata(resolved_url)
                sanitized_metadata = self._sanitize_metadata(metadata)
                return VideoDetails(
                    is_video=True,
                    is_youtube="youtube" in resolved_url,
                    metadata=sanitized_metadata,
                    video_path=str(video_path),
                )

            log.info(
                "Video not in cache, attempting download.", extra={"url": resolved_url}
            )
            metadata = await self._download_video(resolved_url)
            if not metadata:
                log.debug(
                    "No video metadata found after download attempt.",
                    extra={"url": resolved_url},
                )
                return VideoDetails(is_video=False)

            video_path = None
            video_path_str = metadata.get("filepath")
            if not video_path_str and metadata.get("requested_downloads"):
                video_path_str = metadata["requested_downloads"][0].get("filepath")

            if video_path_str:
                video_path = Path(video_path_str)

            if video_path and video_path.exists():
                log.info(
                    "Video downloaded successfully.",
                    extra={"url": resolved_url, "path": str(video_path)},
                )
            log.debug(
                "Video processing complete.",
                extra={
                    "url": resolved_url,
                    "path": str(video_path) if video_path else None,
                },
            )
            sanitized_metadata = self._sanitize_metadata(metadata)
            return VideoDetails(
                is_video=True,
                is_youtube="youtube" in resolved_url,
                metadata=sanitized_metadata,
                video_path=str(video_path) if video_path else None,
            )

        except Exception as e:
            log.error(
                "An unexpected error occurred while processing URL for video.",
                extra={"url": resolved_url, "error": str(e)},
                exc_info=True,
            )
            return VideoDetails(is_video=False)

    def _sanitize_metadata(self, data: Any) -> Any:
        """
        Recursively sanitizes metadata to remove non-serializable objects.
        """
        if isinstance(data, dict):
            return {str(k): self._sanitize_metadata(v) for k, v in data.items()}
        elif isinstance(data, list):
            return [self._sanitize_metadata(item) for item in data]
        elif isinstance(data, (int, float, str, bool)) or data is None:
            return data
        else:
            return str(data)

    async def get_video_info(
        self, url: str, ignore_youtube: bool = False
    ) -> Optional[dict[str, Any]]:
        """
        Extracts video information from a given URL using yt-dlp.

        Args:
            url: The URL of the video.
            ignore_youtube: If True, yt-dlp will not process YouTube URLs.

        Returns:
            An optional dictionary containing video information, or None if extraction fails.
        """
        log.debug("Extracting video info.", extra={"url": url})
        ydl_opts: dict[str, Any] = {
            "executable": Settings.YTDLP_PATH,
            "noplaylist": True,
            "ignoreerrors": True,
            "no_warnings": True,
            "dump_single_json": True,
            "quiet": True,
        }
        if ignore_youtube:
            ydl_opts["match_filter"] = match_filter_func(
                "!is_live & !extractor_key 'Youtube'"
            )

        if "youtube.com" not in url and "youtu.be" not in url:
            ydl_opts["force_generic_extractor"] = True

        try:
            with yt_dlp.YoutubeDL(ydl_opts) as ydl:  # type: ignore
                info = await asyncio.to_thread(ydl.extract_info, url, download=False)  # type: ignore
                log.debug(
                    "Successfully extracted video info.",
                    extra={"url": url, "has_info": info is not None},
                )
                return cast(dict[str, Any], info) if info else None
        except Exception as e:
            log.debug(
                "yt-dlp could not extract info.",
                extra={"url": url, "error": str(e)},
            )
            return None

    async def _extract_metadata(self, url: str) -> Optional[dict[str, Any]]:
        """Extracts video metadata without downloading."""
        log.debug("Extracting metadata.", extra={"url": url})
        try:
            ydl_opts: dict[str, Any] = {
                "executable": Settings.YTDLP_PATH,
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
            log.debug(
                "Metadata extraction result.",
                extra={"url": url, "has_metadata": metadata_raw is not None},
            )
            return cast(dict[str, Any], metadata_raw) if metadata_raw else None
        except YoutubeDLError as e:
            log.error(
                "Failed to extract metadata.", extra={"url": url, "error": str(e)}
            )
            return None

    async def _download_video(self, url: str) -> Optional[dict[str, Any]]:
        """Downloads a video and returns its metadata."""
        log.debug("Downloading video.", extra={"url": url})
        base_path = self.cache_manager.get_cache_base_path_for_url(url)

        ydl_opts: dict[str, Any] = {
            "executable": Settings.YTDLP_PATH,
            "quiet": True,
            "no_warnings": True,
            "noplaylist": True,
            "ignoreerrors": True,
            "outtmpl": f"{base_path}.%(ext)s",
            "format": "bestvideo+bestaudio/best",
        }
        try:
            with yt_dlp.YoutubeDL(ydl_opts) as ydl:  # type: ignore
                metadata_raw = await asyncio.to_thread(
                    ydl.extract_info, url, download=True
                )
            log.debug(
                "Video download result.",
                extra={"url": url, "has_metadata": metadata_raw is not None},
            )
            return cast(dict[str, Any], metadata_raw) if metadata_raw else None
        except YoutubeDLError as e:
            log.error("Failed to download video.", extra={"url": url, "error": str(e)})
            return None
