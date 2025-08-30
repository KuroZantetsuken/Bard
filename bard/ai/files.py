import asyncio
import logging
from collections import defaultdict
from typing import Dict, List, Optional, Tuple

import aiohttp
from google.genai import types as gemini_types

from bard.ai.core import GeminiCore
from bard.bot.types import VideoMetadata
from bard.util.data.parser import extract_image_url_from_html
from bard.util.media.media import MimeDetector
from bard.util.media.video import VideoProcessor
from config import Config

logger = logging.getLogger("Bard")


class AttachmentProcessor:
    """
    Processes and uploads various types of media (images, videos) to the Gemini File API.
    Manages a cache for uploaded files and handles video processing logic.
    """

    _gemini_file_cache: Dict[str, gemini_types.FileData] = {}

    _upload_locks: defaultdict[str, asyncio.Lock] = defaultdict(asyncio.Lock)

    _original_urls: Dict[str, str] = {}

    def __init__(self, gemini_core: GeminiCore):
        """
        Initializes the AttachmentProcessor.

        Args:
            gemini_core: An instance of GeminiCore for interacting with the Gemini API.
        """
        self.gemini_core = gemini_core
        self.video_processor = VideoProcessor()

    async def upload_media_bytes(
        self,
        data_bytes: bytes,
        display_name: str,
        mime_type: str,
        original_url: Optional[str] = None,
    ) -> gemini_types.Part:
        """
        Uploads media bytes to the Gemini File API if not already cached,
        and returns a Gemini Part object referencing the uploaded file.

        Args:
            data_bytes: The raw bytes of the media file.
            display_name: A human-readable name for the file.
            mime_type: The MIME type of the media file.
            original_url: The original public URL of the media, if applicable.

        Returns:
            A gemini_types.Part object with file_data or text indicating an error.
        """
        cache_key = f"{display_name}_{len(data_bytes)}"
        if cache_key in self._gemini_file_cache:
            return gemini_types.Part(file_data=self._gemini_file_cache[cache_key])

        async with self._upload_locks[cache_key]:
            if cache_key in self._gemini_file_cache:
                return gemini_types.Part(file_data=self._gemini_file_cache[cache_key])

            try:
                gemini_part = await self.gemini_core.upload_media_bytes(
                    data_bytes, display_name, mime_type
                )
                if gemini_part.file_data:
                    self._gemini_file_cache[cache_key] = gemini_part.file_data
                    if original_url:
                        self._original_urls[gemini_part.file_data.file_uri] = (
                            original_url
                        )
                return gemini_part
            except Exception as e:
                logger.error(
                    f"Error during media upload for '{display_name}': {e}",
                    exc_info=True,
                )
                return gemini_types.Part(text=f"[Attachment Error: {display_name}]")

    async def process_image_url(self, url: str) -> Optional[gemini_types.Part]:
        """
        Downloads an image from a given URL and processes it for Gemini.
        If the URL returns HTML, it attempts to extract an image URL from the HTML and processes that.

        Args:
            url: The URL of the image or a page containing the image.

        Returns:
            An optional Gemini types.Part object representing the processed image.
        """
        try:
            async with aiohttp.ClientSession() as session:
                async with session.get(url) as response:
                    if response.status != 200:
                        logger.debug(
                            f"Failed to fetch content from {url}: Status {response.status}"
                        )
                        return None

                    content_type = response.headers.get("Content-Type", "").lower()

                    if "text/html" in content_type:
                        html_content = await response.text()
                        extracted_image_url = await extract_image_url_from_html(
                            html_content, url
                        )
                        if extracted_image_url:
                            logger.debug(
                                f"Extracted image URL from HTML: {extracted_image_url}. Attempting to process it."
                            )

                            return await self.process_image_url(extracted_image_url)
                        else:
                            logger.warning(
                                f"No image URL found in HTML content from {url}."
                            )
                            return None
                    elif content_type.startswith("image/"):
                        data = await response.read()
                        mime_type = MimeDetector.detect(data)
                        if not mime_type.startswith("image/"):
                            logger.warning(
                                f"URL {url} did not resolve to an image. Detected MIME type: {mime_type}"
                            )
                            return None
                        return await self.upload_media_bytes(
                            data, "image", mime_type, original_url=url
                        )
                    else:
                        logger.warning(
                            f"URL {url} returned unsupported content type: {content_type}"
                        )
                        return None
        except Exception as e:
            logger.error(f"Error processing image URL {url}: {e}", exc_info=True)
            return None

    def get_original_url(self, gemini_file_uri: str) -> Optional[str]:
        """
        Retrieves the original public URL associated with a Gemini File API URI.

        Args:
            gemini_file_uri: The URI of the file uploaded to the Gemini File API.

        Returns:
            The original public URL as a string, or None if not found.
        """
        return self._original_urls.get(gemini_file_uri)

    async def _get_video_processing_details(
        self, url: str
    ) -> Tuple[Optional[str], List[str], Optional[VideoMetadata], Optional[str]]:
        """
        Fetches video information and determines the processing strategy (full video or audio only).

        Args:
            url: The URL of the video.

        Returns:
            A tuple containing:
            - stream_url (Optional[str]): The URL to stream the video/audio.
            - ffmpeg_args (List[str]): FFmpeg arguments for processing.
            - video_metadata (Optional[VideoMetadata]): Extracted video metadata.
            - mime_type (Optional[str]): The MIME type of the processed content.
        """
        info_dict = await self.video_processor.get_video_info(url)
        if not info_dict or not info_dict.get("duration"):
            logger.debug(f"No video info or duration found for {url}.")
            return None, [], None, None

        video_metadata = self.video_processor._create_video_metadata(url, info_dict)

        if video_metadata.is_youtube:
            logger.debug(f"YouTube video detected for {url}. Returning URL directly.")
            return (
                url,
                [],
                video_metadata,
                "video/mp4",
            )

        duration = video_metadata.duration_seconds or 0
        estimated_tokens = (duration * Config.VIDEO_TOKEN_COST_PER_SECOND) + (
            duration * Config.AUDIO_TOKEN_COST_PER_SECOND
        )

        format_selector = "bestvideo[ext=mp4]+bestaudio[ext=m4a]/best[ext=mp4]/best"
        ffmpeg_args: List[str] = []
        mime_type = "video/mp4"

        if estimated_tokens >= Config.MAX_VIDEO_TOKENS_FOR_FULL_PROCESSING:
            format_selector = "bestaudio[ext=m4a]/bestaudio"
            ffmpeg_args = [
                "-vn",
                "-acodec",
                "libmp3lame",
                "-q:a",
                "2",
                "-f",
                "mp3",
                "pipe:1",
            ]
            mime_type = "audio/mpeg"
            logger.debug(
                f"Video {url} estimated tokens {estimated_tokens} exceeds limit. Processing audio only."
            )
        else:
            logger.debug(
                f"Video {url} estimated tokens {estimated_tokens} within limit. Processing full video."
            )

        stream_url = await self.video_processor.get_stream_url(url, format_selector)

        return stream_url, ffmpeg_args, video_metadata, mime_type

    async def _process_video_url(
        self, url: str
    ) -> Tuple[Optional[gemini_types.Part], Optional[VideoMetadata]]:
        """
        Processes a video from a URL using the VideoProcessor.

        Args:
            url: The URL of the video.

        Returns:
            A tuple containing:
            - Optional[gemini_types.Part]: The Gemini part representing the processed video/audio.
            - Optional[VideoMetadata]: The extracted video metadata.
        """
        (
            stream_url,
            ffmpeg_args,
            video_metadata,
            mime_type,
        ) = await self._get_video_processing_details(url)

        if not video_metadata:
            return None, None

        if video_metadata.is_youtube:
            return (
                gemini_types.Part(file_data=gemini_types.FileData(file_uri=url)),
                video_metadata,
            )

        if not stream_url:
            logger.warning(f"Could not get stream URL for {url}.")
            return None, video_metadata

        if not mime_type:
            logger.warning(f"MIME type not determined for {url}.")
            return None, video_metadata

        media_bytes = await self.video_processor.stream_media(stream_url, ffmpeg_args)
        if not media_bytes:
            logger.warning(f"Could not stream media for {url}.")
            return None, video_metadata

        display_name = (
            f"video_{video_metadata.duration_seconds}s"
            if video_metadata.duration_seconds
            else "video_unknown_duration"
        )
        return (
            await self.upload_media_bytes(media_bytes, display_name, mime_type),
            video_metadata,
        )

    async def check_and_process_url(
        self, url: str
    ) -> Tuple[
        Optional[gemini_types.Part],
        Optional[gemini_types.Part],
        Optional[VideoMetadata],
        Optional[str],
    ]:
        """
        Checks if a URL points to a video or an image, processes it accordingly,
        and returns the appropriate Gemini parts and metadata.

        Args:
            url: The URL to check and process.

        Returns:
            A tuple containing:
            - video_part (Optional[gemini_types.Part]): The Gemini part for a video, if detected.
            - image_part (Optional[gemini_types.Part]): The Gemini part for an image, if detected.
            - video_metadata (Optional[VideoMetadata]): Metadata for a video, if detected.
            - remaining_url (Optional[str]): The URL if it's neither a video nor an image.
        """
        video_part, video_metadata = await self._process_video_url(url)
        if video_part:
            return video_part, None, video_metadata, None

        image_part = await self.process_image_url(url)
        if image_part:
            return None, image_part, None, None

        return None, None, None, url
