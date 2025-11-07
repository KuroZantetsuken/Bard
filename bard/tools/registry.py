import asyncio
import importlib
import importlib.util
import inspect
import logging
import os
import sys
from typing import Any, Dict, List, Optional

from google.genai import types as gemini_types

from bard.tools.base import (AttachmentProcessorProtocol, BaseTool,
                             FFmpegWrapperProtocol, GeminiCoreProtocol,
                             MimeDetectorProtocol, ResponseExtractorProtocol,
                             ToolContext)
from bard.util.logging import LogFormatter, LogSanitizer
from config import Config

logger = logging.getLogger("Bard")


class ToolRegistry:
    """
    Manages the discovery, loading, and execution of tools available to the Gemini model.
    It dynamically loads tools from a specified directory, maintains a registry of
    their capabilities, and provides methods for executing tool functions.
    """

    def __init__(
        self,
        config: Config,
        gemini_core: GeminiCoreProtocol,
        response_extractor: ResponseExtractorProtocol,
        attachment_processor: AttachmentProcessorProtocol,
        ffmpeg_wrapper: FFmpegWrapperProtocol,
        mime_detector: MimeDetectorProtocol,
    ):
        """
        Initializes the ToolRegistry.

        Args:
            config: Application configuration settings.
            gemini_core: An instance of GeminiCoreProtocol.
            response_extractor: An instance of ResponseExtractorProtocol.
            attachment_processor: An instance of AttachmentProcessorProtocol.
            ffmpeg_wrapper: An instance of FFmpegWrapperProtocol.
            mime_detector: An instance of MimeDetectorProtocol.
        """
        self.config = config
        self.gemini_core = gemini_core
        self.response_extractor = response_extractor
        self.attachment_processor = attachment_processor
        self.ffmpeg_wrapper = ffmpeg_wrapper
        self.mime_detector = mime_detector
        self.tools: Dict[str, BaseTool] = {}
        self.tool_emojis: Dict[str, str] = {}
        self.function_to_tool_map: Dict[str, str] = {}
        self.shared_tool_context: Optional[ToolContext] = None
        self._discover_and_load_tools()

    def _discover_and_load_tools(self):
        """
        Discovers and dynamically loads tool modules from the configured tools directory.
        Each tool must inherit from BaseTool.
        """
        tools_dir_name = os.path.basename(self.config.TOOLS_DIR)
        tools_abs_path = os.path.abspath(self.config.TOOLS_DIR)
        if not os.path.isdir(tools_abs_path):
            logger.warning(
                f"Tools directory '{tools_abs_path}' not found. No tools will be loaded."
            )
            return

        self.shared_tool_context = ToolContext(
            config=self.config,
            gemini_core=self.gemini_core,
            response_extractor=self.response_extractor,
            attachment_processor=self.attachment_processor,
            ffmpeg_wrapper=self.ffmpeg_wrapper,
            mime_detector=self.mime_detector,
        )

        for filename in os.listdir(tools_abs_path):
            if (
                filename.endswith(".py")
                and not filename.startswith("_")
                and filename != "__init__.py"
            ):
                if filename == "base.py" or filename == "registry.py":
                    continue
                tool_file_path = os.path.join(tools_abs_path, filename)
                logical_module_name = f"{tools_dir_name}.{filename[:-3]}"
                try:
                    spec = importlib.util.spec_from_file_location(
                        logical_module_name, tool_file_path
                    )
                    if spec is None:
                        logger.error(
                            f"Could not create module spec/module for '{logical_module_name}'. Skipping."
                        )
                        continue
                    module = importlib.util.module_from_spec(spec)
                    if module is None:
                        logger.error(
                            f"Could not create module spec/module for '{logical_module_name}'. Skipping."
                        )
                        continue
                    sys.modules[logical_module_name] = module
                    assert (
                        spec.loader is not None
                    ), f"ModuleSpec loader is None for {logical_module_name}"
                    spec.loader.exec_module(module)
                    for attribute_name in dir(module):
                        attribute = getattr(module, attribute_name)
                        if (
                            inspect.isclass(attribute)
                            and issubclass(attribute, BaseTool)
                            and attribute is not BaseTool
                        ):
                            try:
                                tool_instance = attribute(
                                    context=self.shared_tool_context
                                )
                                if (
                                    hasattr(tool_instance, "tool_emoji")
                                    and tool_instance.tool_emoji
                                ):
                                    self.tool_emojis[attribute.__name__] = (
                                        tool_instance.tool_emoji
                                    )
                                self.tools[attribute.__name__] = tool_instance
                                logger.debug(
                                    f"Successfully loaded {attribute.__name__} from {tools_dir_name} as {logical_module_name}"
                                )

                                for (
                                    func_decl
                                ) in tool_instance.get_function_declarations():
                                    if func_decl.name is None:
                                        logger.warning(
                                            f"Skipping function without name in {attribute.__name__}"
                                        )
                                        continue
                                    if func_decl.name in self.function_to_tool_map:
                                        logger.warning(
                                            f"Duplicate function name '{func_decl.name}'. Overwriting map from {self.function_to_tool_map[func_decl.name]} to {attribute.__name__}."
                                        )
                                    self.function_to_tool_map[func_decl.name] = (
                                        attribute.__name__
                                    )
                            except Exception as e_init:
                                logger.error(
                                    f"Failed to instantiate tool '{attribute.__name__}': {e_init}.",
                                    exc_info=True,
                                )
                except ImportError as e_import:
                    logger.error(
                        f"Failed to import module from file {tool_file_path} (logical name: {logical_module_name}): {e_import}",
                        exc_info=True,
                    )
                except Exception as e_module:
                    logger.error(
                        f"Error processing module file {tool_file_path} (logical name: {logical_module_name}): {e_module}",
                        exc_info=True,
                    )
        logger.info(
            f"Tool discovery complete. Loaded {len(self.tools)} tool classes, mapped {len(self.function_to_tool_map)} functions."
        )

    def get_all_function_declarations(self) -> List[gemini_types.FunctionDeclaration]:
        """
        Retrieves all function declarations from all loaded tools.
        These declarations are provided to the Gemini model to inform it about available tools.

        Returns:
            A list of Gemini FunctionDeclaration objects.
        """
        declarations = []
        for tool_instance in self.tools.values():
            try:
                declarations.extend(tool_instance.get_function_declarations())
            except Exception as e:
                logger.error(
                    f"Error getting function declarations from tool '{tool_instance.__class__.__name__}': {e}",
                    exc_info=True,
                )
        return declarations

    def reset_tool_context_data(self):
        """
        Resets the tool-specific data within the shared ToolContext.
        This method should be called at the beginning of each new conversation turn
        to ensure a clean state for tool interactions.
        """
        if self.shared_tool_context:
            self.shared_tool_context.tool_response_data = {}
            if hasattr(self.shared_tool_context, "grounding_sources_md"):
                del self.shared_tool_context.grounding_sources_md
        else:
            logger.warning(
                "Attempted to reset tool context data, but shared_tool_context is None."
            )

    async def execute_function(
        self, function_name: str, args: Dict[str, Any], context: ToolContext
    ) -> Optional[gemini_types.Part]:
        """
        Executes a specified tool function and ensures the result is a `gemini_types.Part`.
        It looks up the tool, calls its `execute_tool` method, handles timeouts,
        and wraps the result in a `Part` if necessary.

        Args:
            function_name: The name of the function to execute.
            args: A dictionary of arguments to pass to the function.
            context: The ToolContext object to pass to the tool's execution method.

        Returns:
            An optional Gemini types.Part object containing the result of the function execution.
        """
        logger.info(
            f"Executing tool function: {function_name} with args: {LogFormatter.prettify_json(LogSanitizer.clean_dict(args))}"
        )
        tool_class_name = self.function_to_tool_map.get(function_name)
        if not tool_class_name:
            logger.error(f"No registered tool found for function: {function_name}.")
            return gemini_types.Part(
                function_response=gemini_types.FunctionResponse(
                    name=function_name,
                    response={
                        "success": False,
                        "error": f"Function '{function_name}' is not registered. Available functions: {list(self.function_to_tool_map.keys())}",
                    },
                )
            )
        tool_instance = self.tools.get(tool_class_name)
        if not tool_instance:
            logger.error(
                f"Tool instance '{tool_class_name}' not found for function '{function_name}'. Registry inconsistency."
            )
            return gemini_types.Part(
                function_response=gemini_types.FunctionResponse(
                    name=function_name,
                    response={
                        "success": False,
                        "error": f"Internal error: Tool '{tool_class_name}' for function '{function_name}' not found in registry.",
                    },
                )
            )
        try:
            result = await asyncio.wait_for(
                tool_instance.execute_tool(function_name, args, context),
                timeout=context.config.TOOL_TIMEOUT_SECONDS,
            )

            if (
                result
                and hasattr(result, "function_response")
                and result.function_response
                and hasattr(result.function_response, "response")
            ):
                logger.info(
                    f"Tool function {function_name} returned: {LogFormatter.prettify_json(LogSanitizer.clean_dict(result.function_response.response))}"
                )
            else:
                logger.info(
                    f"Tool function {function_name} returned: {LogFormatter.prettify_json(LogSanitizer.clean_dict(result))}"
                )

            if isinstance(result, gemini_types.FunctionResponse):
                return gemini_types.Part(function_response=result)
            elif isinstance(result, gemini_types.Part):
                return result
            else:
                logger.error(
                    f"Tool '{tool_class_name}' function '{function_name}' returned an unexpected type: {type(result)}"
                )
                return gemini_types.Part(
                    function_response=gemini_types.FunctionResponse(
                        name=function_name,
                        response={
                            "success": False,
                            "error": f"Tool returned an unexpected type: {type(result)}",
                        },
                    )
                )

        except asyncio.TimeoutError:
            logger.error(
                f"Function '{function_name}' in tool '{tool_class_name}' timed out after {context.config.TOOL_TIMEOUT_SECONDS} seconds."
            )
            return gemini_types.Part(
                function_response=gemini_types.FunctionResponse(
                    name=function_name,
                    response={"success": False, "error": "Tool execution timed out."},
                )
            )
        except Exception as e:
            logger.error(
                f"Error executing function '{function_name}' in tool '{tool_class_name}': {e}.",
                exc_info=True,
            )
            return gemini_types.Part(
                function_response=gemini_types.FunctionResponse(
                    name=function_name,
                    response={"success": False, "error": f"Execution failed: {str(e)}"},
                )
            )
