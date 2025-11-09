import asyncio
import logging
from collections import defaultdict
from typing import Dict, Optional

from google.genai import types as gemini_types

from ai.core import GeminiCore

log = logging.getLogger("Bard")


class AttachmentProcessor:
    """
    Processes and uploads various types of media (images, videos) to the Gemini File API.
    Manages a cache for uploaded files and handles video processing logic.
    """

    _gemini_file_cache: Dict[str, gemini_types.File] = {}
    _upload_locks: defaultdict[str, asyncio.Lock] = defaultdict(asyncio.Lock)
    _original_urls: Dict[str, str] = {}

    def __init__(self, gemini_core: GeminiCore):
        """
        Initializes the AttachmentProcessor.

        Args:
            gemini_core: An instance of GeminiCore for interacting with the Gemini API.
        """
        log.debug("Initializing AttachmentProcessor")
        self.gemini_core = gemini_core

    async def upload_media_bytes(
        self,
        data_bytes: bytes,
        display_name: str,
        mime_type: str,
        original_url: Optional[str] = None,
    ) -> Optional[gemini_types.File]:
        """
        Uploads media bytes to the Gemini File API if not already cached,
        and returns a Gemini File object.

        Args:
            data_bytes: The raw bytes of the media file.
            display_name: A human-readable name for the file.
            mime_type: The MIME type of the media file.
            original_url: The original public URL of the media, if applicable.

        Returns:
            A gemini_types.File object or None if an error occurred.
        """
        log.debug(
            "Uploading media bytes",
            extra={
                "display_name": display_name,
                "mime_type": mime_type,
                "original_url": original_url,
                "data_bytes_len": len(data_bytes),
            },
        )
        cache_key = f"{display_name}_{len(data_bytes)}"
        if cache_key in self._gemini_file_cache:
            log.info(f"Cache hit for media '{display_name}'.")
            return self._gemini_file_cache[cache_key]

        async with self._upload_locks[cache_key]:
            if cache_key in self._gemini_file_cache:
                log.info(f"Cache hit for media '{display_name}' after acquiring lock.")
                return self._gemini_file_cache[cache_key]

            try:
                log.info(f"Cache miss for media '{display_name}'. Uploading to Gemini.")
                gemini_file = await self.gemini_core.upload_media_bytes(
                    data_bytes, display_name, mime_type
                )
                if gemini_file:
                    self._gemini_file_cache[cache_key] = gemini_file
                    if original_url and gemini_file.uri:
                        self._original_urls[gemini_file.uri] = original_url
                log.debug(
                    "Finished uploading media bytes", extra={"gemini_file": gemini_file}
                )
                return gemini_file
            except Exception as e:
                log.error(
                    f"Error during media upload for '{display_name}': {e}",
                    exc_info=True,
                )
                return None
