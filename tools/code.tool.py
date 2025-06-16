import base64
import json
import logging
import os
from config import Config
from discord_utils import MimeDetector
from gemini_utils import GeminiConfigManager
from gemini_utils import sanitize_response_for_logging
from google.genai import types
from tools import BaseTool
from tools import ToolContext
from typing import Any
from typing import Dict
from typing import List as TypingList
logger = logging.getLogger("Bard")
class CodeExecutionTool(BaseTool):
    """
    A tool to allow the Gemini model to generate and execute Python code in a sandboxed environment.
    This tool makes a secondary call to the Gemini API with the code_execution tool enabled.
    """
    def __init__(self, config: Config):
        self.config = config
    def get_function_declarations(self) -> TypingList[types.FunctionDeclaration]:
        return [
            types.FunctionDeclaration(
                name="execute_python_code",
                description=(
                    "Executes Python code to solve a given task. Provide a **natural language description** of the task to be performed. "
                    "This tool will then generate, execute, and return the results. "
                    "You can use this to aid with logical requests that benefit from code execution. You have specific libraries for basic image editing, chess problem solving, and graph plotting."
                ),
                parameters=types.Schema(
                    type=types.Type.OBJECT,
                    properties={
                        "task": types.Schema(
                            type=types.Type.STRING,
                            description="A clear and specific **natural language instruction** for the task to be accomplished."
                        ),
                        "has_file_input": types.Schema(
                            type=types.Type.BOOLEAN,
                            description="Set to 'true' if the user's prompt includes a file attachment that the code needs to operate on."
                        )
                    },
                    required=["task"],
                )
            )
        ]
    def _create_code_execution_config(self) -> types.GenerateContentConfig:
        """
        Creates the Gemini generation configuration for a code execution call.
        """
        safety_settings = GeminiConfigManager.get_base_safety_settings()
        return types.GenerateContentConfig(
            system_instruction=types.Content(parts=[types.Part(text="Your critical function is to always use code execution.")], role="system"),
            temperature=0.8,
            top_p=0.95,
            max_output_tokens=self.config.MAX_OUTPUT_TOKENS,
            safety_settings=safety_settings,
            tools=[types.Tool(code_execution=types.ToolCodeExecution())],
        )
    async def execute_tool(self, function_name: str, args: Dict[str, Any], context: ToolContext) -> types.Part:
        gemini_client = context.get("gemini_client")
        task_description = args.get("task")
        if not all([gemini_client, task_description]):
            error_msg = "CodeExecutionTool: Missing required context or 'task' argument."
            logger.error(f"❌ {error_msg}")
            return types.Part(function_response=types.FunctionResponse(name=function_name, response={"success": False, "error": error_msg}))
        code_exec_config = self._create_code_execution_config()
        prompt_parts_for_exec = [types.Part(text=task_description)]
        if args.get("has_file_input"):
            original_user_turn = context.get("original_user_turn_content")
            if original_user_turn:
                file_parts = [p for p in original_user_turn.parts if p.file_data]
                if file_parts:
                    prompt_parts_for_exec.extend(file_parts)
                    logger.info(f"⚙️ Injected {len(file_parts)} file(s) into the code execution context.")
        contents_for_code_exec = [types.Content(role="user", parts=prompt_parts_for_exec)]
        logger.info(f"⚙️ CodeExecutionTool: Performing secondary Gemini call for code execution:\n{task_description}")
        try:
            request_payload = sanitize_response_for_logging({
                "model": self.config.MODEL_ID,
                "contents": [c.dict() for c in contents_for_code_exec],
                "config": code_exec_config.dict()
            })
            logger.info(f"REQUEST to Gemini API (code_execution):\n{json.dumps(request_payload, indent=2)}")
            response = await gemini_client.aio.models.generate_content(
                model=self.config.MODEL_ID,
                contents=contents_for_code_exec,
                config=code_exec_config,
            )
            sanitized_response = sanitize_response_for_logging(response.dict())
            logger.info(f"RESPONSE from Gemini API (code_execution):\n{json.dumps(sanitized_response, indent=2)}")
            text_output = ""
            image_generated = False
            generated_filename = None
            if response.candidates:
                for part in response.candidates[0].content.parts:
                    if part.inline_data and part.inline_data.mime_type.startswith("image/"):
                        mime_type = part.inline_data.mime_type
                        extension = MimeDetector.get_extension(mime_type)
                        generated_filename = f"generated_image{extension}"
                        context.image_data = part.inline_data.data
                        context.image_filename = generated_filename
                        image_generated = True
                        context.is_final_output = True
                    elif part.code_execution_result:
                        if part.code_execution_result.output:
                            text_output += part.code_execution_result.output + "\n"
            else:
                 feedback = response.prompt_feedback if response.prompt_feedback else "No candidates returned."
                 logger.error(f"❌ Code execution call failed. Feedback: {feedback}")
                 return types.Part(function_response=types.FunctionResponse(name=function_name, response={"success": False, "error": f"Code execution call failed: {feedback}"}))
            return types.Part(function_response=types.FunctionResponse(
                name=function_name,
                response={"success": True, "output": text_output.strip(), "image_generated": image_generated, "filename": generated_filename}
            ))
        except Exception as e:
            logger.error(f"❌ Unhandled exception during CodeExecutionTool API call: {e}", exc_info=True)
            return types.Part(function_response=types.FunctionResponse(name=function_name, response={"success": False, "error": f"An exception occurred: {e}"}))