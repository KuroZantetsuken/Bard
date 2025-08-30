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
                description="Inspects the project structure and file contents. If a path to a folder is provided, it returns the folder's file hierarchy. If a path to a file is provided, it returns the file's contents.",
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
                hierarchy = self._get_directory_hierarchy(path_arg)
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

    def _get_directory_hierarchy(self, path: str) -> str:
        """
        Generates a string representing the directory hierarchy, excluding dotfiles and dunder directories.
        """
        lines = []
        for root, dirs, files in os.walk(path, topdown=True):
            dirs[:] = [
                d
                for d in dirs
                if not d.startswith(".")
                and not (d.startswith("__") and d.endswith("__"))
            ]

            level = root.replace(path, "").count(os.sep)

            if level == 0 and (
                os.path.basename(root).startswith(".")
                or (
                    os.path.basename(root).startswith("__")
                    and os.path.basename(root).endswith("__")
                )
            ):
                continue

            indent = " " * 4 * level
            lines.append(f"{indent}{os.path.basename(root)}/")

            sub_indent = " " * 4 * (level + 1)

            for f in sorted([f for f in files if not f.startswith(".")]):
                lines.append(f"{sub_indent}{f}")
        return "\n".join(lines)
