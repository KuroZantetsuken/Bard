import logging
import mimetypes
import re
from typing import List

import magic

# Initialize logger for the media utility module.
logger = logging.getLogger("Bard")


def extract_urls(text: str) -> List[str]:
    """
    Extracts all URLs from a text block using a generalized regular expression pattern.

    Args:
        text: The input text from which to extract URLs.

    Returns:
        A list of extracted URLs.
    """
    url_regex = re.compile(r"https?://[^\s/$.?#].[^\s]*", re.IGNORECASE)
    return url_regex.findall(text)


class MimeDetector:
    """
    Utility class for detecting MIME types from binary data and determining file extensions.
    """

    @classmethod
    def detect(cls, data: bytes) -> str:
        """
        Detects the MIME type of given binary data.

        Args:
            data: The binary data (bytes) for which to detect the MIME type.

        Returns:
            A string representing the detected MIME type, or "application/octet-stream" on failure.
        """
        try:
            return magic.from_buffer(data, mime=True)
        except Exception as e:
            logger.error(f"MIME detection failed: {e}", exc_info=True)
            return "application/octet-stream"

    @classmethod
    def get_extension(cls, mime_type: str) -> str:
        """
        Returns the common file extension for a given MIME type.

        Args:
            mime_type: The MIME type string (e.g., "image/png").

        Returns:
            A string representing the file extension (e.g., ".png"), or ".bin" if no extension is found.
        """
        if not mime_type:
            return ".bin"
        return mimetypes.guess_extension(mime_type) or ".bin"
