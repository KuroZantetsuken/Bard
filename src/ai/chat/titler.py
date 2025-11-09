import logging
from typing import Optional

from google.genai import types

from ai.config import GeminiConfigManager
from ai.core import GeminiCore
from settings import Settings

log = logging.getLogger("Bard")


class ThreadTitler:
    """
    A service for generating a concise and relevant title for a given text using a specialized AI call.
    This is used to dynamically name threads created by the bot.
    """

    def __init__(
        self,
        gemini_core: GeminiCore,
        gemini_config_manager: GeminiConfigManager,
        settings: Settings,
    ):
        """
        Initializes the ThreadTitler service.

        Args:
            gemini_core: The core Gemini client for making API calls.
            gemini_config_manager: The configuration manager for creating specialized AI call settings.
            settings: The application configuration.
        """
        log.debug("Initializing ThreadTitler")
        self.gemini_core = gemini_core
        self.gemini_config_manager = gemini_config_manager
        self.settings = settings

    async def generate_title(self, text_content: str) -> Optional[str]:
        """
        Generates a title for the given text content using a specialized AI call.

        Args:
            text_content: The text content to generate a title for.

        Returns:
            The generated title as a string, or None if title generation fails.
        """
        log.info(
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

            log.debug(
                f"REQUEST to Gemini (model: {self.settings.MODEL_ID_SECONDARY})",
                extra={
                    "model": self.settings.MODEL_ID_SECONDARY,
                    "contents": [c.model_dump() for c in contents],
                    "generation_config": title_config.model_dump(),
                },
            )

            response = await self.gemini_core.generate_content(
                model=self.settings.MODEL_ID_SECONDARY,
                contents=contents,
                config=title_config,
            )

            log.debug(
                f"RESPONSE from Gemini (model: {self.settings.MODEL_ID_SECONDARY})",
                extra={"response": response.model_dump()},
            )

            if response and response.text:
                title = response.text.strip()

                final_title = title[:100]
                log.info(f"Successfully generated thread title: '{final_title}'")
                return final_title
            else:
                log.warning("Thread title generation failed: No text in response.")
                return None
        except Exception as e:
            log.error(f"Error generating thread title: {e}", exc_info=True)
            return None
