import logging
import os
from typing import Any, Dict, List

from google.genai import types

from bard.tools.base import BaseTool, ToolContext

logger = logging.getLogger("Bard")


class DiagnoseTool(BaseTool):
    """
    A tool for the bot to inspect its own project files.
    """

    tool_emoji = "🔍"

    def __init__(self, context: ToolContext):
        """
        Initializes the DiagnoseTool.

        Args:
            context: The ToolContext object providing shared resources.
        """
        super().__init__(context=context)

    def get_function_declarations(self) -> List[types.FunctionDeclaration]:
        """
        Returns the function declarations for the `inspect_project` function.
        """
        return [
            types.FunctionDeclaration(
                name="inspect_project",
                description="Purpose: This tool allows the AI to inspect its own project structure and file contents, which is essential for self-diagnosis, understanding the existing codebase, and verifying changes. It provides a way for the AI to dynamically explore its own environment. Results: If a path to a folder is provided, the tool returns a JSON object representing the folder's file hierarchy, including nested files and directories. If a path to a file is provided, it returns the raw content of that file as a string. Arguments: This function accepts a `path` argument, which is the relative path to the file or folder to be inspected. Use '.' to inspect the project's root directory. Restrictions/Guidelines: Use this tool when you need to understand the structure of the project or the content of a specific file. It is a read-only tool and does not modify any files or directories. Do not use this tool to execute code or interact with external services.",
                parameters=types.Schema(
                    type=types.Type.OBJECT,
                    properties={
                        "path": types.Schema(
                            type=types.Type.STRING,
                            description='The path to a file or folder to inspect. Use "." for the project root.',
                        )
                    },
                    required=["path"],
                ),
            )
        ]

    async def execute_tool(
        self, function_name: str, args: Dict[str, Any], context: ToolContext
    ) -> types.Part:
        """
        Executes the `inspect_project` function.
        """
        path_arg = args.get("path", ".")

        try:
            if os.path.isfile(path_arg):
                with open(path_arg, "r", encoding="utf-8") as f:
                    content = f.read()
                return types.Part(
                    function_response=self.function_response_success(
                        function_name, f"Contents of file: {path_arg}", content=content
                    )
                )
            elif os.path.isdir(path_arg):
                hierarchy = self._get_directory_json(path_arg)
                return types.Part(
                    function_response=self.function_response_success(
                        function_name,
                        f"Directory hierarchy of: {path_arg}",
                        hierarchy=hierarchy,
                    )
                )
            else:
                return types.Part(
                    function_response=self.function_response_error(
                        function_name, f"Path not found: {path_arg}"
                    )
                )
        except Exception as e:
            logger.error(f"Error inspecting path '{path_arg}': {e}", exc_info=True)
            return types.Part(
                function_response=self.function_response_error(
                    function_name, f"An unexpected error occurred: {e}"
                )
            )

    def _get_directory_json(self, path: str) -> Dict[str, Any]:
        """
        Generates a dictionary representing the directory hierarchy.
        """
        # Get the absolute path to handle "." and other relative paths correctly
        abs_path = os.path.abspath(path)

        # Create the root of the structure
        dir_structure = {
            "name": os.path.basename(abs_path),
            "path": path,
            "type": "directory",
            "children": [],
        }

        # Walk through the directory
        for root, dirs, files in os.walk(path, topdown=True):
            # Filter out dot-prefixed and dunder-style directories
            dirs[:] = [
                d
                for d in dirs
                if not d.startswith(".")
                and not (d.startswith("__") and d.endswith("__"))
            ]

            # Find the current directory's node in the structure
            parts = os.path.relpath(root, path).split(os.sep)
            if parts[0] == ".":
                current_level = dir_structure["children"]
            else:
                # Find the correct place in the tree to add new nodes
                node = dir_structure
                for part in parts:
                    # Find the child that matches the part
                    child_node = next(
                        (
                            child
                            for child in node.get("children", [])
                            if child["name"] == part
                        ),
                        None,
                    )
                    if child_node:
                        node = child_node
                    # This case should ideally not be hit if os.walk works as expected
                current_level = node.get("children", [])

            # Add subdirectories
            for d in sorted(dirs):
                current_level.append(
                    {
                        "name": d,
                        "path": os.path.join(os.path.relpath(root, "."), d),
                        "type": "directory",
                        "children": [],
                    }
                )

            # Add files, filtering out dot-prefixed files
            for f in sorted([f for f in files if not f.startswith(".")]):
                current_level.append(
                    {
                        "name": f,
                        "path": os.path.join(os.path.relpath(root, "."), f),
                        "type": "file",
                    }
                )

        return dir_structure
