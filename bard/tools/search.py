import logging
from typing import Any, Dict, List

from google.genai import types

from bard.ai.config import GeminiConfigManager
from bard.tools.base import BaseTool, ToolContext
from bard.util.logging import LogFormatter, LogSanitizer

logger = logging.getLogger("Bard")


class SearchTool(BaseTool):
    """
    A tool that enables the Gemini model to perform explicit web searches.
    """

    tool_emoji = "🌐"

    def __init__(self, context: ToolContext):
        """
        Initializes the SearchTool.

        Args:
            context: The ToolContext object providing shared resources.
        """
        super().__init__(context=context)

    def get_function_declarations(self) -> List[types.FunctionDeclaration]:
        """
        Returns the function declarations for the `search_internet` function.
        """
        return [
            types.FunctionDeclaration(
                name="search_internet",
                description=(
                    "Purpose: This powerful tool enables the AI to access external, real-time information from the internet. This is crucial for answering factual questions, retrieving current events, and understanding content that are not part of the AI's internal knowledge base. Arguments: This function accepts a `search_query` argument, which is a clear and concise query for internet search. Results: Upon execution, this tool returns a concise, summarized overview of the information retrieved from web searches. This summary includes relevant snippets of information and, critically, provides markdown-formatted source links for full attribution and user verification, allowing users to easily access the original data sources. Restrictions/Guidelines: This tool should be primarily used for tasks that require up-to-date information or external factual verification. It is explicitly designed to expand the AI's knowledge beyond its training data. Conversely, this tool must not be used for retrieving information that is already part of the AI's internal knowledge, or for tasks that can be solved computationally or logically by other internal tools."
                ),
                parameters=types.Schema(
                    type=types.Type.OBJECT,
                    properties={
                        "search_query": types.Schema(
                            type=types.Type.STRING,
                            description="A clear and concise query for the web search.",
                        )
                    },
                    required=["search_query"],
                ),
            )
        ]

    def _create_tooling_config(self) -> types.GenerateContentConfig:
        """
        Creates the Gemini generation configuration for an internal tooling call,
        enabling built-in tools like Google Search.
        """
        available_tools = [
            types.Tool(google_search=types.GoogleSearch()),
        ]
        safety_settings = GeminiConfigManager.get_base_safety_settings()
        config = types.GenerateContentConfig(
            system_instruction=types.Content(
                parts=[
                    types.Part(
                        text="Critical directive: search the internet and provide a verbose summary."
                    )
                ],
                role="system",
            ),
            temperature=1.0,
            top_p=0.95,
            max_output_tokens=self.context.config.MAX_OUTPUT_TOKENS,
            safety_settings=safety_settings,
            tools=available_tools,
        )
        try:
            config.thinking_config = types.ThinkingConfig(
                include_thoughts=False,
                thinking_budget=self.context.config.THINKING_BUDGET,
            )
        except AttributeError:
            logger.warning(
                "Gemini SDK version might not support 'thinking_config' for tooling. Proceeding without it."
            )
        return config

    def _create_internet_tool_internal_prompt(
        self, search_query: str
    ) -> List[types.Content]:
        """
        Creates the internal prompt for the secondary Gemini call that uses internet tools.

        Args:
            search_query: The query for the web search.

        Returns:
            A list of Gemini Content objects forming the prompt.
        """
        prompt_parts = []

        prompt_parts.append(
            types.Part(
                text=f"Please use the internet tools to accomplish the following task: {search_query}. Provide a verbose summary."
            )
        )
        return [types.Content(parts=prompt_parts, role="user")]

    async def execute_tool(
        self, function_name: str, args: Dict[str, Any], context: ToolContext
    ) -> types.Part:
        """
        Executes the `search_internet` function.

        Args:
            function_name: The name of the function to execute (expected to be "search_internet").
            args: A dictionary containing the `search_query` argument.
            context: The ToolContext object providing shared resources.

        Returns:
            A Gemini types.Part object containing the function response.
        """
        if function_name != "search_internet":
            return types.Part(
                function_response=types.FunctionResponse(
                    name=function_name,
                    response={
                        "success": False,
                        "error": f"Unknown function in SearchTool: {function_name}",
                    },
                )
            )

        gemini_core = context.gemini_core
        response_extractor = context.response_extractor

        if not gemini_core or not response_extractor:
            missing_services = []
            if not gemini_core:
                missing_services.append("gemini_core")
            if not response_extractor:
                missing_services.append("response_extractor")
            error_msg = (
                f"Missing required context variables: {', '.join(missing_services)}."
            )
            logger.error(f"SearchTool: {error_msg}")
            return types.Part(
                function_response=types.FunctionResponse(
                    name=function_name, response={"success": False, "error": error_msg}
                )
            )

        search_query = args.get("search_query")
        if not search_query:
            error_msg = "Missing 'search_query' argument."
            logger.error(f"SearchTool: {error_msg}")
            return types.Part(
                function_response=types.FunctionResponse(
                    name=function_name, response={"success": False, "error": error_msg}
                )
            )

        tooling_gen_config = self._create_tooling_config()
        try:
            contents_for_tooling_call = self._create_internet_tool_internal_prompt(
                search_query
            )
            request_payload = {
                "model": self.context.config.MODEL_ID,
                "contents": [c.model_dump() for c in contents_for_tooling_call],
                "config": tooling_gen_config.model_dump(),
            }
            logger.debug(
                f"Gemini API (native_tools) request:\n{LogFormatter.prettify_json(LogSanitizer.clean_dict(request_payload))}"
            )

            logger.info("Calling Gemini API for search tool.")
            tooling_response = await gemini_core.generate_content(
                model=self.context.config.MODEL_ID,
                contents=contents_for_tooling_call,
                config=tooling_gen_config,
            )
            logger.info("Finished calling Gemini API for search tool.")
            logger.debug(
                f"Gemini API (native_tools) response:\n{LogFormatter.prettify_json(LogSanitizer.clean_dict(tooling_response.model_dump()))}"
            )
            if not tooling_response.candidates:
                details = (
                    f"Prompt Feedback: {tooling_response.prompt_feedback}"
                    if tooling_response.prompt_feedback
                    else "No details provided."
                )
                logger.error(
                    f"Built-in tools call was blocked or failed. Details: {details}."
                )
                error_msg = f"Built-in tools call was blocked or failed. {details}"
                logger.error(f"Built-in tools call failed: {error_msg}")
                return types.Part(
                    function_response=types.FunctionResponse(
                        name=function_name,
                        response={"success": False, "error": error_msg},
                    )
                )
            candidate = tooling_response.candidates[0]
            if not candidate.finish_reason:
                error_msg = "Built-in tools call returned an incomplete response (missing finish reason)."
                logger.error(
                    f"Built-in tools call incomplete: {error_msg} Candidate: {candidate}"
                )
                return types.Part(
                    function_response=types.FunctionResponse(
                        name=function_name,
                        response={"success": False, "error": error_msg},
                    )
                )

            tooling_text_result = response_extractor.extract_response(tooling_response)

            response_data = {
                "tool_output": (
                    tooling_text_result
                    if tooling_text_result
                    else "No textual output from tools."
                )
            }

            if (
                candidate.grounding_metadata
                and candidate.grounding_metadata.grounding_chunks
            ):
                chunks = candidate.grounding_metadata.grounding_chunks
                if chunks:
                    source_links = []
                    unique_sources = {}
                    for chunk in chunks:
                        if (
                            hasattr(chunk, "web")
                            and hasattr(chunk.web, "uri")
                            and hasattr(chunk.web, "title")
                            and chunk.web.uri
                            and chunk.web.title
                        ):
                            if chunk.web.uri not in unique_sources:
                                title = chunk.web.title.replace("]", "").replace(
                                    "[", ""
                                )
                                unique_sources[chunk.web.uri] = (
                                    f"[{title}](<{chunk.web.uri}>)"
                                )
                    if unique_sources:
                        source_links = list(unique_sources.values())
                        grounding_sources_md = "-# " + ", ".join(source_links)
                        setattr(context, "grounding_sources_md", grounding_sources_md)

            return types.Part(
                function_response=types.FunctionResponse(
                    name=function_name, response=response_data
                )
            )
        except Exception as e_tool:
            logger.error(
                f"Error during 'search_internet' secondary API call: {e_tool}.",
                exc_info=True,
            )
            return types.Part(
                function_response=types.FunctionResponse(
                    name=function_name,
                    response={
                        "success": False,
                        "error": f"Tooling call failed: {str(e_tool)}",
                    },
                )
            )
