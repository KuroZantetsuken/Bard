import logging
from typing import Optional

from google.genai import types

from bard.ai.config.settings import GeminiConfigManager
from bard.ai.core import GeminiCore
from bard.util.logging import prettify_json_for_logging
from config import Config

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

            prompt = f"Generate a title for the following content:\n\n{text_content}"
            contents = [types.Content(parts=[types.Part(text=prompt)])]

            loggable_request_payload = {
                "model": self.config.MODEL_ID_SECONDARY,
                "contents": [c.model_dump() for c in contents],
                "generation_config": title_config.model_dump(),
            }
            logger.debug(
                f"REQUEST to Gemini (model: {self.config.MODEL_ID_SECONDARY}):\n"
                f"{prettify_json_for_logging(loggable_request_payload)}"
            )

            response = await self.gemini_core.generate_content(
                model=self.config.MODEL_ID_SECONDARY,
                contents=contents,
                config=title_config,
            )

            loggable_response = response.model_dump()
            logger.debug(
                f"RESPONSE from Gemini (model: {self.config.MODEL_ID_SECONDARY}):\n"
                f"{prettify_json_for_logging(loggable_response)}"
            )

            if response and response.text:
                title = response.text.strip()

                final_title = title[:100]
                logger.debug(f"Successfully generated thread title: '{final_title}'")
                return final_title
            else:
                logger.warning("Thread title generation failed: No text in response.")
                return None
        except Exception as e:
            logger.error(f"Error generating thread title: {e}", exc_info=True)
            return None
