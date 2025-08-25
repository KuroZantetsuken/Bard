import logging
from typing import Optional

from google.genai import types

from ai.core import GeminiCore
from ai.settings import GeminiConfigManager
from config import Config
from utilities.logging import prettify_json_for_logging

# Initialize logger for the thread titler module.
logger = logging.getLogger("Bard")


class ThreadTitler:
    """
    A service for generating a concise and relevant title for a given text using a specialized AI call.
    This is used to dynamically name threads created by the bot.
    """

    def __init__(
        self,
        gemini_core: GeminiCore,
        gemini_config_manager: GeminiConfigManager,
        config: Config,
    ):
        """
        Initializes the ThreadTitler service.

        Args:
            gemini_core: The core Gemini client for making API calls.
            gemini_config_manager: The configuration manager for creating specialized AI call settings.
            config: The application configuration.
        """
        self.gemini_core = gemini_core
        self.gemini_config_manager = gemini_config_manager
        self.config = config

    async def generate_title(self, text_content: str) -> Optional[str]:
        """
        Generates a title for the given text content using a specialized AI call.

        Args:
            text_content: The text content to generate a title for.

        Returns:
            The generated title as a string, or None if title generation fails.
        """
        logger.debug(
            f"Starting thread title generation for text content (length: {len(text_content)})."
        )
        try:
            # Create a specialized configuration for title generation.
            safety_settings = GeminiConfigManager.get_base_safety_settings()
            title_config = types.GenerateContentConfig(
                system_instruction=types.Content(
                    parts=[
                        types.Part(
                            text="You are an expert at creating concise, descriptive, and appropriate titles for discussion threads. Generate a title of 100 characters MAXIMUM for the following content."
                        )
                    ],
                    role="system",
                ),
                max_output_tokens=50,
                safety_settings=safety_settings,
            )

            # Create the prompt for the AI call.
            prompt = f"Generate a title for the following content:\n\n{text_content}"
            contents = [types.Content(parts=[types.Part(text=prompt)])]

            # Log the request payload before sending to Gemini.
            loggable_request_payload = {
                "model": self.config.MODEL_ID_TITLER,
                "contents": [c.model_dump() for c in contents],
                "generation_config": title_config.model_dump(),
            }
            logger.debug(
                f"REQUEST to Gemini (model: {self.config.MODEL_ID_TITLER}):\n"
                f"{prettify_json_for_logging(loggable_request_payload)}"
            )

            # Make the AI call to generate the title.
            response = await self.gemini_core.generate_content(
                model=self.config.MODEL_ID_TITLER,
                contents=contents,
                config=title_config,
            )

            # Log the response from Gemini.
            loggable_response = response.model_dump()
            logger.debug(
                f"RESPONSE from Gemini (model: {self.config.MODEL_ID_TITLER}):\n"
                f"{prettify_json_for_logging(loggable_response)}"
            )

            # Extract the title from the response.
            if response and response.text:
                title = response.text.strip()
                # Ensure the title is within the 100-character limit.
                final_title = title[:100]
                logger.debug(f"Successfully generated thread title: '{final_title}'")
                return final_title
            else:
                logger.warning("Thread title generation failed: No text in response.")
                return None
        except Exception as e:
            logger.error(f"Error generating thread title: {e}", exc_info=True)
            return None
