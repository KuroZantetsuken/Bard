import asyncio
import logging
from collections import defaultdict
from typing import Dict, Optional

from google.genai import types as gemini_types

from bard.ai.core import GeminiCore
from bard.util.media.video import VideoProcessor

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

    def get_original_url(self, gemini_file_uri: str) -> Optional[str]:
        """
        Retrieves the original public URL associated with a Gemini File API URI.

        Args:
            gemini_file_uri: The URI of the file uploaded to the Gemini File API.

        Returns:
            The original public URL as a string, or None if not found.
        """
        return self._original_urls.get(gemini_file_uri)
