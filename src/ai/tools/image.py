import json
import logging
import mimetypes
from typing import Any, Dict, List

from google.genai import types

from ai.tools.base import BaseTool, ToolContext

log = logging.getLogger("Bard")


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
                    "Generate a high-quality image based on a detailed text description. Use this tool for creative tasks requiring visual output, such ascreating illustrations, photorealistic scenes, logos, or other visual assets. Provide a comprehensive and descriptive prompt to guide the image generation process. Arguments: This function accepts a `prompt` argument, which is a string containing the detailed description for the image to be generated. Results: Upon successful generation, this tool returns information about the generated image, including its filename and confirmation of generation."
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
        log.info(f"Executing tool '{function_name}'")
        log.debug("Tool arguments", extra={"tool_args": args})
        if function_name != "generate_image":
            error_msg = f"Unknown function: {function_name}"
            log.error(f"ImageGenerationTool: {error_msg}")
            return types.Part(
                function_response=self.function_response_error(function_name, error_msg)
            )

        gemini_core = context.get("gemini_core")
        if not gemini_core:
            error_msg = "Missing 'gemini_core' from context."
            log.error(f"ImageGenerationTool: {error_msg}")
            return types.Part(
                function_response=self.function_response_error(function_name, error_msg)
            )

        prompt = args.get("prompt")
        if not prompt:
            error_msg = "Missing 'prompt' argument."
            log.error(f"ImageGenerationTool: {error_msg}")
            return types.Part(
                function_response=self.function_response_error(function_name, error_msg)
            )

        try:
            image_model_id = context.settings.MODEL_ID_IMAGE_GENERATION
            log.info(
                f"Calling Gemini API for image generation with model: {image_model_id}"
            )
            log.debug("Image generation details", extra={"prompt": prompt})

            response = await gemini_core.aio.models.generate_content(
                model=image_model_id,
                contents=[prompt],
            )
            log.debug(
                "Received response from Gemini API for image generation",
                extra={"response": response.model_dump()},
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
                            extension = mimetypes.guess_extension(mime_type) or ".bin"
                            generated_filename = f"generated_image{extension}"

                            self.context.tool_response_data["image_data"] = (
                                part.inline_data.data
                            )
                            self.context.tool_response_data["image_filename"] = (
                                generated_filename
                            )
                            self.context.is_final_output = True
                            image_generated = True
                            log.debug(
                                "Image data found in response",
                                extra={
                                    "generated_filename": generated_filename,
                                    "mime_type": mime_type,
                                    "data_len": len(part.inline_data.data),
                                },
                            )
                            break
                    if image_generated:
                        break
            else:
                feedback = response.prompt_feedback
                feedback_str = str(feedback) if feedback else "No candidates returned."
                log.error(f"Image generation failed. Feedback: {feedback}.")
                return types.Part(
                    function_response=self.function_response_error(
                        function_name, feedback_str
                    )
                )

            if image_generated:
                log.info(f"Successfully generated image: {generated_filename}")
                return types.Part(
                    function_response=self.function_response_success(
                        function_name,
                        "Image generated successfully.",
                        image_generated=True,
                        filename=generated_filename,
                    )
                )
            else:
                log.warning(
                    "No image data found in Gemini response despite successful API call."
                )
                return types.Part(
                    function_response=self.function_response_error(
                        function_name, "No image data found in the response."
                    )
                )

        except json.JSONDecodeError:
            error_msg = "The AI server returned an invalid JSON response (likely empty). This may indicate the model is not supported or the server is experiencing issues."
            log.error(f"ImageGenerationTool: {error_msg}")
            return types.Part(
                function_response=self.function_response_error(function_name, error_msg)
            )
        except Exception as e:
            error_msg = str(e)
            log.error(
                f"Unhandled exception during ImageGenerationTool API call: {error_msg}.",
                exc_info=True,
            )
            return types.Part(
                function_response=self.function_response_error(
                    function_name, f"An exception occurred: {error_msg}"
                )
            )
