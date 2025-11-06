import logging
from typing import Any

from google.genai import types as gemini_types

logger = logging.getLogger("Bard")


class ResponseExtractor:
    """
    Extracts textual content from a Gemini API response.
    This utility class provides a static method for extracting relevant information
    from various Gemini response structures.
    """

    @staticmethod
    def extract_response(response: Any) -> str:
        """
        Attempts to extract textual content from a Gemini API response or Content object.
        It handles different types of response objects by checking for 'text' attributes
        or 'parts' within a 'Content' object.

        Args:
            response: The Gemini API response object or a Gemini types.Content object.

        Returns:
            A string containing the extracted text content. Returns an empty string
            if no text content can be extracted.
        """
        if hasattr(response, "text"):
            extracted_text = response.text
        elif (
            isinstance(response, gemini_types.Content)
            and hasattr(response, "parts")
            and response.parts is not None
        ):
            extracted_text = "".join(
                [
                    part.text
                    for part in response.parts
                    if hasattr(part, "text") and part.text is not None
                ]
            )
        elif isinstance(response, str):
            extracted_text = response
        else:
            extracted_text = ""
        return extracted_text
