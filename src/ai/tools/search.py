import logging
from typing import Any, Dict, List

from google.genai import types

from ai.config import GeminiConfigManager
from ai.tools.base import BaseTool, ToolContext

log = logging.getLogger("Bard")


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
                    "Purpose: This powerful tool enables the AI to access external, real-time information from the internet. This is crucial for answering factual questions, retrieving current events, and understanding content that are not part of the AI's internal knowledge base. Arguments: This function accepts a `search_query` argument, which is a clear and concise query for internet search. Results: Upon execution, this tool returns a concise, summarized overview of the information retrieved from web searches. This summary includes relevant snippets of information and, critically, provides markdown-formatted source links for full attribution and user verification, allowing users to easily access the original data sources. Restrictions/Guidelines: This tool should be primarily used for tasks that require up-to-date information or external factual verification. It is explicitly designed to expand the AI's knowledge beyond its training data. Conversely, this tool must not be used for retrieving information that is already part of the AI's internal knowledge, or for tasks that can be solved computationally or logically by other internal tools. Always preserve the citations and insert them inline during your final answer exactly how the tool returns its answer to you, including the URL markdown masking in this format: [[1]](<https://vertexaisearch.cloud.google.com/grounding-api-redirect/...>)."
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
            max_output_tokens=self.context.settings.MAX_OUTPUT_TOKENS,
            safety_settings=safety_settings,
            tools=available_tools,
        )
        try:
            config.thinking_config = types.ThinkingConfig(
                include_thoughts=False,
                thinking_budget=self.context.settings.THINKING_BUDGET,
            )
        except AttributeError:
            log.warning(
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

    @staticmethod
    def _extract_response(response: Any) -> str:
        """
        Attempts to extract textual content from a Gemini API response or Content object.
        It handles different types of response objects by checking for 'text' attributes
        or 'parts' within a 'Content' object.

        Args:
            response: The Gemini API response object or a Gemini types.Content object.

        Returns:
            A string containing the extracted text content. Returns an empty string
            if no text content can be extracted.
        """
        if hasattr(response, "text"):
            extracted_text = response.text
        elif (
            isinstance(response, types.Content)
            and hasattr(response, "parts")
            and response.parts is not None
        ):
            extracted_text = "".join(
                [
                    part.text
                    for part in response.parts
                    if hasattr(part, "text") and part.text is not None
                ]
            )
        elif isinstance(response, str):
            extracted_text = response
        else:
            extracted_text = ""
        return extracted_text

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
        log.info(f"Executing tool '{function_name}'")
        log.debug("Tool arguments", extra={"tool_args": args})
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
        if not gemini_core:
            missing_services = []
            if not gemini_core:
                missing_services.append("gemini_core")
            error_msg = (
                f"Missing required context variables: {', '.join(missing_services)}."
            )
            log.error(f"SearchTool: {error_msg}")
            return types.Part(
                function_response=types.FunctionResponse(
                    name=function_name, response={"success": False, "error": error_msg}
                )
            )

        search_query = args.get("search_query")
        if not search_query:
            error_msg = "Missing 'search_query' argument."
            log.error(f"SearchTool: {error_msg}")
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
            log.debug(
                "Sending search request to Gemini",
                extra={
                    "model": self.context.settings.MODEL_ID,
                    "contents": [c.model_dump() for c in contents_for_tooling_call],
                    "config": tooling_gen_config.model_dump(),
                },
            )

            log.info("Calling Gemini API for search tool.")
            tooling_response = await gemini_core.generate_content(
                model=self.context.settings.MODEL_ID,
                contents=contents_for_tooling_call,
                config=tooling_gen_config,
            )
            log.info("Finished calling Gemini API for search tool.")
            log.debug(
                "Received search response from Gemini",
                extra={"response": tooling_response.model_dump()},
            )
            if not tooling_response.candidates:
                details = (
                    f"Prompt Feedback: {tooling_response.prompt_feedback}"
                    if tooling_response.prompt_feedback
                    else "No details provided."
                )
                log.error(
                    f"Built-in tools call was blocked or failed. Details: {details}."
                )
                error_msg = f"Built-in tools call was blocked or failed. {details}"
                log.error(f"Built-in tools call failed: {error_msg}")
                return types.Part(
                    function_response=types.FunctionResponse(
                        name=function_name,
                        response={"success": False, "error": error_msg},
                    )
                )
            candidate = tooling_response.candidates[0]
            if not candidate.finish_reason:
                error_msg = "Built-in tools call returned an incomplete response (missing finish reason)."
                log.error(
                    f"Built-in tools call incomplete: {error_msg} Candidate: {candidate}"
                )
                return types.Part(
                    function_response=types.FunctionResponse(
                        name=function_name,
                        response={"success": False, "error": error_msg},
                    )
                )

            tooling_text_result = self._extract_response(tooling_response)

            response_data = {
                "tool_output": (
                    tooling_text_result
                    if tooling_text_result
                    else "No textual output from tools."
                )
            }

            if (
                candidate.grounding_metadata
                and candidate.grounding_metadata.grounding_supports
                and candidate.grounding_metadata.grounding_chunks
            ):
                text = tooling_text_result
                supports = candidate.grounding_metadata.grounding_supports
                chunks = candidate.grounding_metadata.grounding_chunks

                sorted_supports = sorted(
                    supports, key=lambda s: s.segment.end_index, reverse=True
                )

                for support in sorted_supports:
                    end_index = support.segment.end_index
                    if support.grounding_chunk_indices:
                        citation_links = []
                        for i in support.grounding_chunk_indices:
                            if i < len(chunks):
                                uri = chunks[i].web.uri
                                citation_links.append(f"[[{i + 1}]](<{uri}>)")

                        if citation_links:
                            citation_string = "".join(citation_links)
                            text = text[:end_index] + citation_string + text[end_index:]

                tooling_text_result = text

            response_data["tool_output"] = tooling_text_result
            return types.Part(
                function_response=types.FunctionResponse(
                    name=function_name, response=response_data
                )
            )
        except Exception as e_tool:
            log.error(
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
