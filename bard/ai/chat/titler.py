import json
import logging
from typing import Optional

from google.genai import types

from bard.ai.config import GeminiConfigManager
from bard.ai.core import GeminiCore
from bard.ai.schemas import ThreadTitle
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
        Generates a title for the given text content using a specialized AI call with JSON mode.

        Args:
            text_content: The text content to generate a title for.

        Returns:
            The generated title as a string, or None if title generation fails.
        """
        logger.debug(
            f"Starting thread title generation for text content (length: {len(text_content)})."
        )
        try:
            title_config = types.GenerationConfig(
                response_mime_type="application/json",
                response_schema=ThreadTitle,  # type: ignore
                max_output_tokens=2000,
                temperature=0.7,
            )

            system_instruction = types.Content(
                parts=[
                    types.Part(
                        text="You are an expert at creating concise, descriptive, and appropriate titles for discussion threads. Your response must be a JSON object that conforms to the provided schema."
                    )
                ],
                role="system",
            )

            prompt = f"Generate a title for the following content:\n\n{text_content}"
            contents = [types.Content(parts=[types.Part(text=prompt)])]

            loggable_request_payload = {
                "model": self.config.MODEL_ID_SECONDARY,
                "contents": [c.model_dump() for c in contents],
                "generation_config": title_config.model_dump(),
                "system_instruction": system_instruction.model_dump(),
            }
            logger.debug(
                f"REQUEST to Gemini (model: {self.config.MODEL_ID_SECONDARY}):\n"
                f"{prettify_json_for_logging(loggable_request_payload)}"
            )

            response = await self.gemini_core.generate_content(
                model=self.config.MODEL_ID_SECONDARY,
                contents=contents,
                generation_config=title_config,
                system_instruction=system_instruction,
            )

            loggable_response = response.model_dump()
            logger.debug(
                f"RESPONSE from Gemini (model: {self.config.MODEL_ID_SECONDARY}):\n"
                f"{prettify_json_for_logging(loggable_response)}"
            )

            if response and response.text:
                # The response text is a JSON string, so we parse it.
                response_json = json.loads(response.text)
                thread_title = ThreadTitle(**response_json)
                final_title = thread_title.title
                logger.debug(f"Successfully generated thread title: '{final_title}'")
                return final_title
            else:
                logger.warning("Thread title generation failed: No text in response.")
                return None
        except (json.JSONDecodeError, TypeError) as e:
            logger.error(f"Error parsing JSON response for thread title: {e}")
            return None
        except Exception as e:
            logger.error(f"Error generating thread title: {e}", exc_info=True)
            return None
