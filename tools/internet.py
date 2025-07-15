import logging
from typing import Any, Dict, List, Optional

from google.genai import types

from ai.settings import GeminiConfigManager
from tools.base import AttachmentProcessorProtocol, BaseTool, ToolContext
from utilities.logging import prettify_json_for_logging, sanitize_response_for_logging

# Initialize logger for the internet tool module.
logger = logging.getLogger("Bard")


class InternetTool(BaseTool):
    """
    A tool that enables the Gemini model to access external, real-time information from the internet.
    This includes performing web searches and analyzing URL content.
    """

    tool_emoji = "🌐"

    def __init__(self, context: ToolContext):
        """
        Initializes the InternetTool.

        Args:
            context: The ToolContext object providing shared resources.
        """
        super().__init__(context=context)

    def get_function_declarations(self) -> List[types.FunctionDeclaration]:
        """
        Returns the function declarations for the `use_built_in_tools` function.
        This function is exposed to the Gemini model to allow it to call this tool.
        """
        return [
            types.FunctionDeclaration(
                name="use_built_in_tools",
                description=(
                    "Purpose: This powerful tool enables the AI to access external, real-time information from the internet. This is crucial for answering factual questions, retrieving current events, and understanding content from specific web pages that are not part of the AI's internal knowledge base. Arguments: This function accepts a `search_query` argument, which is a clear and concise query for internet search or URL analysis. Results: Upon execution, this tool returns a concise, summarized overview of the information retrieved from web searches or analyzed URL content. This summary includes relevant snippets of information and, critically, provides markdown-formatted source links for full attribution and user verification, allowing users to easily access the original data sources. Restrictions/Guidelines: This tool should be primarily used for tasks that require up-to-date information, external factual verification, or the analysis of provided web content (excluding video content, which is handled by other specialized tools). It is explicitly designed to expand the AI's knowledge beyond its training data. Conversely, this tool must not be used for retrieving information that is already part of the AI's internal knowledge, or for tasks that can be solved computationally or logically by other internal tools like the CodeExecutionTool."
                ),
                parameters=types.Schema(
                    type=types.Type.OBJECT,
                    properties={
                        "search_query": types.Schema(
                            type=types.Type.STRING,
                            description="A clear and concise query for internet search or URL analysis.",
                        )
                    },
                    required=["search_query"],
                ),
            )
        ]

    def _create_tooling_config(self) -> types.GenerateContentConfig:
        """
        Creates the Gemini generation configuration for an internal tooling call,
        enabling built-in tools like Google Search and URL Context.
        """
        available_tools = [
            types.Tool(google_search=types.GoogleSearch()),
            types.Tool(url_context=types.UrlContext()),
        ]
        safety_settings = GeminiConfigManager.get_base_safety_settings()
        config = types.GenerateContentConfig(
            system_instruction=types.Content(
                parts=[
                    types.Part(
                        text="Critical directive: search the internet or analyze URLs and provide a verbose summary."
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
        This prompt guides the model to utilize its internet search capabilities.

        Args:
            search_query: The query for internet search or URL analysis.

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
        Executes the `use_built_in_tools` function.
        This involves making a secondary Gemini call to perform internet searches
        or URL analysis and processing the results.

        Args:
            function_name: The name of the function to execute (expected to be "use_built_in_tools").
            args: A dictionary containing the `search_query` argument.
            context: The ToolContext object providing shared resources.

        Returns:
            A Gemini types.Part object containing the function response, including
            success status, tool output, and any grounding sources.
        """
        if function_name != "use_built_in_tools":
            return types.Part(
                function_response=types.FunctionResponse(
                    name=function_name,
                    response={
                        "success": False,
                        "error": f"Unknown function in InternetTool: {function_name}",
                    },
                )
            )

        gemini_client = context.gemini_client
        response_extractor = context.response_extractor
        attachment_processor: AttachmentProcessorProtocol = context.attachment_processor

        if not gemini_client or not response_extractor or not attachment_processor:
            missing_services = []
            if not gemini_client:
                missing_services.append("gemini_client")
            if not response_extractor:
                missing_services.append("response_extractor")
            if not attachment_processor:
                missing_services.append("attachment_processor")
            error_msg = (
                f"Missing required context variables: {', '.join(missing_services)}."
            )
            logger.error(f"InternetTool: {error_msg}")
            return types.Part(
                function_response=types.FunctionResponse(
                    name=function_name, response={"success": False, "error": error_msg}
                )
            )

        search_query = args.get("search_query")
        if not search_query:
            error_msg = "Missing 'search_query' argument."
            logger.error(f"InternetTool: {error_msg}")
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
            request_payload = sanitize_response_for_logging(
                {
                    "model": self.context.config.MODEL_ID,
                    "contents": [c.dict() for c in contents_for_tooling_call],
                    "config": tooling_gen_config.dict(),
                }
            )
            logger.debug(
                f"Gemini API (native_tools) request:\n{prettify_json_for_logging(request_payload)}"
            )

            tooling_response = await gemini_client.generate_content(
                model=self.context.config.MODEL_ID,
                contents=contents_for_tooling_call,
                config=tooling_gen_config,
            )
            logger.debug(
                f"Gemini API (native_tools) response:\n{prettify_json_for_logging(tooling_response.model_dump())}"
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
            extracted_image_url_gemini: Optional[str] = None
            extracted_image_url_public: Optional[str] = None

            # Attempt to find an image URL from grounding URIs
            grounding_uris = []
            if (
                candidate.grounding_metadata
                and candidate.grounding_metadata.grounding_chunks
            ):
                for chunk in candidate.grounding_metadata.grounding_chunks:
                    if (
                        hasattr(chunk, "web")
                        and hasattr(chunk.web, "uri")
                        and chunk.web.uri
                    ):
                        grounding_uris.append(chunk.web.uri)

            # Check grounding URIs for images using AttachmentProcessor
            for uri in grounding_uris:
                image_part = await attachment_processor.process_image_url(uri)
                if (
                    image_part
                    and image_part.file_data
                    and image_part.file_data.file_uri
                ):
                    extracted_image_url_gemini = image_part.file_data.file_uri
                    extracted_image_url_public = attachment_processor.get_original_url(
                        extracted_image_url_gemini
                    )
                    logger.debug(
                        f"Successfully extracted image URL (Gemini): {extracted_image_url_gemini}"
                    )
                    if extracted_image_url_public:
                        logger.debug(
                            f"Original public image URL: {extracted_image_url_public}"
                        )
                    break  # Found an image, no need to check further URIs

            response_data = {
                "tool_output": tooling_text_result
                if tooling_text_result
                else "No textual output from tools."
            }
            if extracted_image_url_gemini:
                response_data["image_url_gemini"] = extracted_image_url_gemini
            if extracted_image_url_public:
                response_data["image_url_public"] = extracted_image_url_public

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
                f"Error during 'use_built_in_tools' secondary API call: {e_tool}.",
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
