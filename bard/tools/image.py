import logging
from typing import Any, Dict, List

from google.genai import types

from bard.tools.base import BaseTool, ToolContext
from bard.util.logging import clean_dict, prettify_json_for_logging

logger = logging.getLogger("Bard")


class ImageGenerationTool(BaseTool):
    """
    A tool that allows the Gemini model to generate images based on text prompts.
    It handles image generation and the processing of results.
    """

    tool_emoji = "🎨"

    def __init__(self, context: ToolContext):
        """
        Initializes the ImageGenerationTool.

        Args:
            context: The ToolContext object providing shared resources.
        """
        super().__init__(context=context)

    def get_function_declarations(self) -> List[types.FunctionDeclaration]:
        """
        Returns the function declarations for the `generate_image` function.
        This function is exposed to the Gemini model to allow it to call this tool.
        """
        return [
            types.FunctionDeclaration(
                name="generate_image",
                description=(
                    "Generate a high-quality image based on a detailed text description. "
                    "Use this tool for creative tasks requiring visual output, such as "
                    "creating illustrations, photorealistic scenes, logos, or other visual assets. "
                    "Provide a comprehensive and descriptive prompt to guide the image generation process. "
                    "Example: 'A photorealistic close-up portrait of an elderly Japanese ceramicist with deep, sun-etched wrinkles and a warm, knowing smile. He is carefully inspecting a freshly glazed tea bowl. The scene is illuminated by soft, golden hour light streaming through a window.' "
                    "Arguments: This function accepts a `prompt` argument, which is a string containing the detailed description for the image to be generated."
                    "Results: Upon successful generation, this tool returns information about the generated image, including its filename and confirmation of generation."
                ),
                parameters=types.Schema(
                    type=types.Type.OBJECT,
                    properties={
                        "prompt": types.Schema(
                            type=types.Type.STRING,
                            description="A detailed text description for the image to be generated.",
                        )
                    },
                    required=["prompt"],
                ),
            )
        ]

    async def execute_tool(
        self, function_name: str, args: Dict[str, Any], context: ToolContext
    ) -> types.Part:
        """
        Executes the `generate_image` function.
        This involves calling the Gemini API to generate an image and processing its output.

        Args:
            function_name: The name of the function to execute (expected to be "generate_image").
            args: A dictionary containing the `prompt` argument.
            context: The ToolContext object providing shared resources.

        Returns:
            A Gemini types.Part object containing the function response, including
            success status and generated image details.
        """
        if function_name != "generate_image":
            error_msg = f"Unknown function: {function_name}"
            logger.error(f"ImageGenerationTool: {error_msg}")
            return types.Part(
                function_response=self.function_response_error(function_name, error_msg)
            )

        gemini_client = context.get("gemini_client")
        if not gemini_client:
            error_msg = "Missing 'gemini_client' from context."
            logger.error(f"ImageGenerationTool: {error_msg}")
            return types.Part(
                function_response=self.function_response_error(function_name, error_msg)
            )

        prompt = args.get("prompt")
        if not prompt:
            error_msg = "Missing 'prompt' argument."
            logger.error(f"ImageGenerationTool: {error_msg}")
            return types.Part(
                function_response=self.function_response_error(function_name, error_msg)
            )

        try:
            image_model_id = context.config.MODEL_ID_IMAGE_GENERATION
            request_payload = {
                "model": image_model_id,
                "contents": [prompt],
            }
            logger.debug(
                f"ImageGenerationTool: Calling Gemini API with model: {image_model_id}, prompt: '{prompt}'"
            )
            logger.debug(
                f"Gemini API (image_generation) request:\n{prettify_json_for_logging(clean_dict(request_payload))}"
            )

            response = await gemini_client.aio.models.generate_content(
                model=image_model_id,
                contents=[prompt],
            )
            logger.debug(
                f"ImageGenerationTool: Received response from Gemini API. Candidates: {len(response.candidates) if response.candidates else 0}"
            )
            logger.debug(
                f"Gemini API (image_generation) response:\n{prettify_json_for_logging(clean_dict(response.dict()))}"
            )

            generated_filename = None
            image_generated = False

            if response.candidates:
                for candidate in response.candidates:
                    for part in candidate.content.parts:
                        if part.inline_data and part.inline_data.mime_type.startswith(
                            "image/"
                        ):
                            mime_type = part.inline_data.mime_type
                            extension = self.context.mime_detector.get_extension(
                                mime_type
                            )
                            generated_filename = f"generated_image{extension}"

                            self.context.tool_response_data["image_data"] = (
                                part.inline_data.data
                            )
                            self.context.tool_response_data["image_filename"] = (
                                generated_filename
                            )
                            self.context.is_final_output = True
                            image_generated = True
                            logger.debug(
                                f"ImageGenerationTool: Image data found in response. Filename: {generated_filename}, Mime Type: {mime_type}, Data Length: {len(part.inline_data.data)} bytes."
                            )
                            break
                    if image_generated:
                        break
            else:
                feedback = response.prompt_feedback
                feedback_str = str(feedback) if feedback else "No candidates returned."
                logger.error(f"Image generation failed. Feedback: {feedback}.")
                return types.Part(
                    function_response=self.function_response_error(
                        function_name, feedback_str
                    )
                )

            if image_generated:
                logger.debug(
                    f"ImageGenerationTool: Successfully generated image: {generated_filename}"
                )
                return types.Part(
                    function_response=self.function_response_success(
                        function_name,
                        "Image generated successfully.",
                        image_generated=True,
                        filename=generated_filename,
                    )
                )
            else:
                logger.warning(
                    "ImageGenerationTool: No image data found in Gemini response despite successful API call."
                )
                return types.Part(
                    function_response=self.function_response_error(
                        function_name, "No image data found in the response."
                    )
                )

        except Exception as e:
            error_msg = str(e)
            logger.error(
                f"Unhandled exception during ImageGenerationTool API call: {error_msg}.",
                exc_info=True,
            )
            return types.Part(
                function_response=self.function_response_error(
                    function_name, f"An exception occurred: {error_msg}"
                )
            )
