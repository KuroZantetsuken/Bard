import importlib.util
import os
import sys
project_root = os.path.dirname(os.path.abspath(__file__))
project_root = os.path.dirname(project_root)
if project_root not in sys.path:
    sys.path.insert(0, project_root)
base_tool_filename = "base.tool.py"
base_tool_module_name = "tools.base.tool"
base_tool_filepath = os.path.join(os.path.dirname(__file__), base_tool_filename)
if not os.path.exists(base_tool_filepath):
    raise ImportError(f"The file '{base_tool_filepath}' was not found. Cannot initialize BaseTool and ToolContext.")
try:
    spec = importlib.util.spec_from_file_location(base_tool_module_name, base_tool_filepath)
    if spec is None:
        raise ImportError(f"Could not create module spec for '{base_tool_module_name}' from '{base_tool_filepath}'.")
    _base_tool_module = importlib.util.module_from_spec(spec)
    if _base_tool_module is None:
        raise ImportError(f"Could not create module from spec for '{base_tool_module_name}'.")
    sys.modules[base_tool_module_name] = _base_tool_module
    if spec.loader is None:
        raise ImportError(f"Could not find loader for module '{base_tool_module_name}'.")
    spec.loader.exec_module(_base_tool_module)
    BaseTool = _base_tool_module.BaseTool
    ToolContext = _base_tool_module.ToolContext
except AttributeError as e:
    raise ImportError(
        f"Could not find 'BaseTool' or 'ToolContext' in module loaded from '{base_tool_filepath}'. "
        f"Ensure these are defined in '{base_tool_filename}'. Original error: {e}"
    ) from e
except Exception as e:
    raise ImportError(
        f"An unexpected error occurred while trying to load '{base_tool_filename}' as module '{base_tool_module_name}'. "
        f"File: '{base_tool_filepath}'. Original error: {e}"
    ) from e
__all__ = ["BaseTool", "ToolContext"]
