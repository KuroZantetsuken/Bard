import logging
import base64
from typing import Any, Dict, List as TypingList
from tools import BaseTool, ToolContext
from google.genai import types
from gemini_utils import GeminiConfigManager
from config import Config
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
                    "Executes Python code to solve a given task. You should provide a **natural language description** of the task to be performed. "
                    "This tool will then generate, execute, and return the results. "
                    "**You MUST NOT provide Python code in the 'task' argument.** "
                    "You can read data from uploaded text and CSV files. You can generate plots and graphs using the `matplotlib` library, which will be returned as inline images."
                    "Available libraries: `attrs`, `chess`, `contourpy`, `fpdf`, `geopandas`, `imageio`, `jinja2`, `joblib`, `jsonschema`, `jsonschema-specifications`, `lxml`, `matplotlib`, `mpmath`, `numpy`, `opencv-python`, `openpyxl`, `packaging`, `pandas`, `pillow`, `protobuf`, `pylatex`, `pyparsing`, `PyPDF2`, `python-dateutil`, `python-docx`, `python-pptx`, `reportlab`, `scikit-learn`, `scipy`, `seaborn`, `six`, `striprtf`, `sympy`, `tabulate`, `tensorflow`, `toolz`, `xlrd`"
                ),
                parameters=types.Schema(
                    type=types.Type.OBJECT,
                    properties={
                        "task": types.Schema(
                            type=types.Type.STRING,
                            description="A clear and specific **natural language instruction** for the task to be accomplished. For example: 'Create a heart-shaped graph and save it to output.png.'"
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
            response = await gemini_client.aio.models.generate_content(
                model=self.config.MODEL_ID,
                contents=contents_for_code_exec,
                config=code_exec_config,
            )
            text_output = ""
            image_generated = False
            if response.candidates:
                for part in response.candidates[0].content.parts:
                    if part.inline_data and part.inline_data.mime_type.startswith("image/"):
                        context.image_data = part.inline_data.data
                        image_generated = True
                        logger.info(f"✅ Found and processed inline_data image artifact (MIME: {part.inline_data.mime_type}).")
                    elif part.code_execution_result:
                        if part.code_execution_result.output:
                            text_output += part.code_execution_result.output + "\n"
            else:
                 feedback = response.prompt_feedback if response.prompt_feedback else "No candidates returned."
                 logger.error(f"❌ Code execution call failed. Feedback: {feedback}")
                 return types.Part(function_response=types.FunctionResponse(name=function_name, response={"success": False, "error": f"Code execution call failed: {feedback}"}))
            return types.Part(function_response=types.FunctionResponse(
                name=function_name,
                response={"success": True, "output": text_output.strip(), "image_generated": image_generated}
            ))
        except Exception as e:
            logger.error(f"❌ Unhandled exception during CodeExecutionTool API call: {e}", exc_info=True)
            return types.Part(function_response=types.FunctionResponse(name=function_name, response={"success": False, "error": f"An exception occurred: {e}"}))