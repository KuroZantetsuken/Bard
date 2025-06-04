import os
import logging
import inspect
import importlib.util
import importlib
import sys
from typing import Any, Dict, List, Optional, Type
from tools import BaseTool, ToolContext
from google.genai import types as gemini_types
from config import Config
logger = logging.getLogger("Bard")
class ToolRegistry:
    def __init__(self, config: Config):
        self.config = config
        self.tools: Dict[str, BaseTool] = {}
        self.function_to_tool_map: Dict[str, str] = {}
        self._discover_and_load_tools()
    def _discover_and_load_tools(self):
        tools_dir_name = os.path.basename(self.config.TOOLS_DIR)
        tools_abs_path = os.path.abspath(self.config.TOOLS_DIR)
        if not os.path.isdir(tools_abs_path):
            logger.warning(f"🛠️ Tools directory '{tools_abs_path}' not found. No tools will be loaded.")
            return
        for filename in os.listdir(tools_abs_path):
            if filename.endswith(".tool.py") and not filename.startswith("_"):
                if filename == "base.tool.py":
                    continue
                tool_file_path = os.path.join(tools_abs_path, filename)
                logical_module_name = f"{tools_dir_name}.{filename[:-3]}"
                try:
                    spec = importlib.util.spec_from_file_location(logical_module_name, tool_file_path)
                    if spec is None:
                        logger.error(f"❌ Could not create module spec for '{logical_module_name}' from '{tool_file_path}'. Skipping.")
                        continue
                    module = importlib.util.module_from_spec(spec)
                    if module is None:
                        logger.error(f"❌ Could not create module from spec for '{logical_module_name}'. Skipping.")
                        continue
                    sys.modules[logical_module_name] = module
                    spec.loader.exec_module(module)
                    for attribute_name in dir(module):
                        attribute = getattr(module, attribute_name)
                        if inspect.isclass(attribute) and \
                           issubclass(attribute, BaseTool) and \
                           attribute is not BaseTool:
                            try:
                                tool_instance = attribute(self.config)
                                self.tools[attribute.__name__] = tool_instance
                                logger.info(f"🛠️ Successfully loaded {attribute.__name__} from {tools_dir_name} as {logical_module_name}")
                                for func_decl in tool_instance.get_function_declarations():
                                    if func_decl.name in self.function_to_tool_map:
                                        logger.warning(f"🛠️ Duplicate function name '{func_decl.name}'. Mapping from {self.function_to_tool_map[func_decl.name]} overwritten by {attribute.__name__}.")
                                    self.function_to_tool_map[func_decl.name] = attribute.__name__
                            except Exception as e_init:
                                logger.error(f"❌ Failed to instantiate tool '{attribute.__name__}' from {logical_module_name}: {e_init}", exc_info=True)
                except ImportError as e_import:
                    logger.error(f"❌ Failed to import module from file {tool_file_path} (logical name: {logical_module_name}): {e_import}", exc_info=True)
                except Exception as e_module:
                    logger.error(f"❌ Error processing module file {tool_file_path} (logical name: {logical_module_name}): {e_module}", exc_info=True)
        logger.info(f"🛠️ Tool discovery complete. Loaded {len(self.tools)} tool classes. Mapped {len(self.function_to_tool_map)} functions.")
    def get_all_function_declarations(self) -> List[gemini_types.FunctionDeclaration]:
        declarations = []
        for tool_instance in self.tools.values():
            try:
                declarations.extend(tool_instance.get_function_declarations())
            except Exception as e:
                logger.error(f"❌ Error getting function declarations from tool '{tool_instance.__class__.__name__}': {e}", exc_info=True)
        return declarations
    async def execute_function(self, function_name: str, args: Dict[str, Any], context: ToolContext) -> Optional[gemini_types.Part]:
        tool_class_name = self.function_to_tool_map.get(function_name)
        if not tool_class_name:
            logger.error(f"❌ No tool found to handle function: {function_name}")
            return gemini_types.Part(function_response=gemini_types.FunctionResponse(
                name=function_name,
                response={"success": False, "error": f"Function '{function_name}' is not recognized or no tool handles it."}
            ))
        tool_instance = self.tools.get(tool_class_name)
        if not tool_instance:
            logger.error(f"❌ Tool instance '{tool_class_name}' not found for function '{function_name}'. Internal inconsistency.")
            return gemini_types.Part(function_response=gemini_types.FunctionResponse(
                name=function_name,
                response={"success": False, "error": f"Internal error: Tool instance for '{function_name}' not found."}
            ))
        try:
            logger.info(f"⚙️ Executing function '{function_name}' via tool '{tool_class_name}' with args: {args}")
            return await tool_instance.execute_tool(function_name, args, context)
        except Exception as e:
            logger.error(f"❌ Error executing function '{function_name}' in tool '{tool_class_name}': {e}", exc_info=True)
            return gemini_types.Part(function_response=gemini_types.FunctionResponse(
                name=function_name,
                response={"success": False, "error": f"Execution of '{function_name}' failed: {str(e)}"}
            ))
    def get_memory_manager(self) -> Optional[Any]:
        memory_tool_instance = self.tools.get("MemoryTool")
        if memory_tool_instance and hasattr(memory_tool_instance, "memory_manager"):
            return memory_tool_instance.memory_manager
        logger.warning("🧠 MemoryManager not available (MemoryTool not loaded or misconfigured).")
        return None