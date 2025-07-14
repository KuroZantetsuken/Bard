import logging
from typing import Any, Dict, List, Optional

from google.genai import types as gemini_types

# Initialize logger for the Gemini settings module.
logger = logging.getLogger("Bard")


class GeminiConfigManager:
    """
    Manages the generation configuration for Gemini API calls.
    This includes setting parameters like temperature, top_p, max_output_tokens,
    safety settings, and integrating tool declarations and system instructions.
    """

    def __init__(self, max_output_tokens: int, thinking_budget: int):
        """
        Initializes the GeminiConfigManager.

        Args:
            max_output_tokens: The maximum number of tokens to generate in the response.
            thinking_budget: The token budget for Gemini's internal "thinking" process when using tools.
        """
        self.max_output_tokens = max_output_tokens
        self.thinking_budget = thinking_budget

    @staticmethod
    def get_base_safety_settings() -> List[gemini_types.SafetySetting]:
        """
        Returns a list of base safety settings configured to block no harm categories.
        This provides maximum flexibility for responses.
        """
        return [
            gemini_types.SafetySetting(
                category=cat, threshold=gemini_types.HarmBlockThreshold.BLOCK_NONE
            )
            for cat in [
                gemini_types.HarmCategory.HARM_CATEGORY_HATE_SPEECH,
                gemini_types.HarmCategory.HARM_CATEGORY_DANGEROUS_CONTENT,
                gemini_types.HarmCategory.HARM_CATEGORY_SEXUALLY_EXPLICIT,
                gemini_types.HarmCategory.HARM_CATEGORY_HARASSMENT,
            ]
        ]

    def create_config(
        self,
        system_instruction_str: Optional[str] = None,
        tool_declarations: Optional[List[gemini_types.FunctionDeclaration]] = None,
    ) -> gemini_types.GenerateContentConfig:
        """
        Creates a Gemini content generation configuration object.

        Args:
            system_instruction_str: An optional string providing system-level instructions to the model.
            tool_declarations: An optional list of FunctionDeclaration objects for tools the model can use.

        Returns:
            A `gemini_types.GenerateContentConfig` object configured with the specified parameters.
        """
        config_args: Dict[str, Any] = {
            "temperature": 1.0,
            "top_p": 0.95,
            "max_output_tokens": self.max_output_tokens,
            "safety_settings": self.get_base_safety_settings(),
        }
        if tool_declarations:
            config_args["tools"] = [
                gemini_types.Tool(function_declarations=tool_declarations)
            ]
        if system_instruction_str:
            config_args["system_instruction"] = system_instruction_str
        config = gemini_types.GenerateContentConfig(**config_args)
        try:
            # Attempt to set thinking_config if supported by the SDK version.
            config.thinking_config = gemini_types.ThinkingConfig(
                include_thoughts=False, thinking_budget=self.thinking_budget
            )
        except AttributeError:
            logger.warning("Gemini SDK version might not support 'thinking_config'.")
        return config
