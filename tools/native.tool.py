import logging
from config import Config
from gemini_utils import GeminiConfigManager
from google.genai import client as genai_client
from google.genai import types
from tools import BaseTool
from tools import ToolContext
from typing import Any
from typing import Dict
from typing import List as TypingList
logger = logging.getLogger("Bard")
class NativeTool(BaseTool):
    def __init__(self, config: Config):
        self.config = config
    def get_function_declarations(self) -> TypingList[types.FunctionDeclaration]:
        return [
            types.FunctionDeclaration(
                name="use_built_in_tools",
                description=(
                    "Use this function when you need to access Google Search for current information or "
                    "analyze the content of a web URL provided by the user. "
                    "This function takes no arguments. The system will automatically use the original user "
                    "request for the search or analysis. After this function is called, the system will "
                    "provide you with the result from the tool (e.g., search findings or URL summary). "
                    "You should then use this information to formulate your response to the user."
                ),
                parameters=types.Schema(type=types.Type.OBJECT, properties={})
            )
        ]
    def _create_tooling_config(self) -> types.GenerateContentConfig:
        """
        Creates the Gemini generation configuration for the internal tooling call,
        enabling built-in tools like Google Search and URL Context.
        """
        available_tools = [
            types.Tool(google_search=types.GoogleSearch()),
            types.Tool(url_context=types.UrlContext()),
        ]
        safety_settings = GeminiConfigManager.get_base_safety_settings()
        config = types.GenerateContentConfig(
            system_instruction=types.Content(parts=[types.Part(text="Your critical function is to always search the internet or analyze URLs for extra information.")], role="system"),
            temperature=1.0,
            top_p=0.95,
            max_output_tokens=self.config.MAX_OUTPUT_TOKENS,
            safety_settings=safety_settings,
            tools=available_tools,
        )
        try:
            config.thinking_config = types.ThinkingConfig(
                 include_thoughts=False,
                 thinking_budget=self.config.THINKING_BUDGET
            )
        except AttributeError:
            logger.warning("⚠️ Gemini SDK version might not support 'thinking_config' for tooling. Proceeding without it.")
        return config
    async def execute_tool(self, function_name: str, args: Dict[str, Any], context: ToolContext) -> types.Part:
        if function_name != "use_built_in_tools":
            return types.Part(function_response=types.FunctionResponse(
                name=function_name,
                response={"success": False, "error": f"Unknown function in NativeTool: {function_name}"}
            ))
        gemini_client = context.get("gemini_client")
        response_extractor = context.get("response_extractor")
        original_user_turn_content = context.get("original_user_turn_content")
        history_for_tooling_call = context.get("history_for_tooling_call")
        if not all([gemini_client, response_extractor, original_user_turn_content, history_for_tooling_call is not None]):
            missing = [
                name for name, var in {
                    "gemini_client": gemini_client,
                    "response_extractor": response_extractor,
                    "original_user_turn_content": original_user_turn_content,
                    "history_for_tooling_call": "Provided" if history_for_tooling_call is not None else None
                }.items() if var is None
            ]
            error_msg = f"NativeTool: Missing required context variables: {', '.join(missing)}."
            logger.error(f"❌ {error_msg}")
            return types.Part(function_response=types.FunctionResponse(
                name=function_name,
                response={"success": False, "error": error_msg}
            ))
        logger.info("🛠️ NativeTool: Performing secondary Gemini call for built-in tools: using original prompt")
        tooling_gen_config = self._create_tooling_config()
        try:
            contents_for_tooling_call: TypingList[types.Content] = []
            if history_for_tooling_call:
                contents_for_tooling_call.extend(history_for_tooling_call)
            contents_for_tooling_call.append(original_user_turn_content)
            tooling_response = await gemini_client.aio.models.generate_content(
                model=self.config.MODEL_ID,
                contents=contents_for_tooling_call,
                config=tooling_gen_config,
            )
            tooling_text_result = response_extractor.extract_text(tooling_response)
            if not tooling_response.candidates or tooling_response.candidates[0].finish_reason.name != "STOP":
                reason = "Unknown"
                details = ""
                if not tooling_response.candidates:
                    reason = "No candidates returned"
                    if tooling_response.prompt_feedback:
                         details = f"Prompt Feedback: {tooling_response.prompt_feedback}"
                else:
                    candidate = tooling_response.candidates[0]
                    reason = candidate.finish_reason.name
                    if candidate.finish_reason.name == "SAFETY":
                        details = f"Safety Ratings: {candidate.safety_ratings}"
                logger.error(f"❌ Built-in tools call stopped by API. Finish Reason: {reason}. {details}")
                return None
            logger.info(f"🛠️ Built-in tools call result:\n{tooling_text_result}")
            return types.Part(function_response=types.FunctionResponse(
                name=function_name,
                response={"tool_output": tooling_text_result if tooling_text_result else "No textual output from tools."}
            ))
        except Exception as e_tool:
            logger.error(f"❌ NativeTool: Error during 'use_built_in_tools' secondary API call: {e_tool}", exc_info=True)
            return types.Part(function_response=types.FunctionResponse(
                name=function_name,
                response={"success": False, "error": f"Tooling call failed: {str(e_tool)}"}
            ))