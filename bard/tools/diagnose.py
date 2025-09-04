import logging
import os
import re
from typing import Any, Dict, List

from google.genai import types

from bard.tools.base import BaseTool, ToolContext

logger = logging.getLogger("Bard")


class DiagnoseTool(BaseTool):
    """
    A tool for the bot to inspect its own project files.
    """

    tool_emoji = "🔍"
    _ignored_patterns: List[re.Pattern] = []
    _allowed_exceptions: List[re.Pattern] = []

    def __init__(self, context: ToolContext):
        """
        Initializes the DiagnoseTool.

        Args:
            context: The ToolContext object providing shared resources.
        """
        super().__init__(context=context)
        self._load_gitignore_patterns()

    def _load_gitignore_patterns(self):
        """
        Loads and compiles .gitignore patterns, handling exceptions.
        """
        gitignore_path = ".gitignore"
        if os.path.exists(gitignore_path):
            with open(gitignore_path, "r", encoding="utf-8") as f:
                for line in f:
                    line = line.strip()
                    if not line or line.startswith("#"):
                        continue

                    if line.startswith("!"):
                        pattern = line[1:]
                        if pattern.endswith("/"):
                            pattern = pattern.rstrip("/")
                        self._allowed_exceptions.append(
                            re.compile(self._convert_gitignore_to_regex(pattern))
                        )
                    else:
                        if line.endswith("/"):
                            line = line.rstrip("/")
                        self._ignored_patterns.append(
                            re.compile(self._convert_gitignore_to_regex(line))
                        )

        if not any(p.pattern == r"^logs(/.*)?$" for p in self._allowed_exceptions):
            self._allowed_exceptions.append(re.compile(r"^logs(/.*)?$"))

    def _convert_gitignore_to_regex(self, pattern: str) -> str:
        """
        Converts a .gitignore pattern to a regular expression.
        Handles '*' and '**' wildcards, and ensures patterns match paths correctly.
        """
        regex = re.escape(pattern)
        regex = regex.replace(r"\*\*", r".*")
        regex = regex.replace(r"\*", r"[^/]*")

        if pattern.startswith("/"):
            regex = "^" + regex
        else:
            regex = "(^|/)" + regex

        if pattern.endswith("/"):
            regex = regex + "(/.*)?$"
        else:
            regex = regex + "$"

        return regex

    def _is_ignored_file(self, file_path: str) -> bool:
        """
        Checks if a file path should be ignored based on .gitignore patterns.
        """
        normalized_path = os.path.normpath(file_path)

        for pattern in self._allowed_exceptions:
            if pattern.match(normalized_path):
                return False

        for pattern in self._ignored_patterns:
            if pattern.match(normalized_path):
                return True

        return False

    def get_function_declarations(self) -> List[types.FunctionDeclaration]:
        """
        Returns the function declarations for the `inspect_project` function.
        """
        return [
            types.FunctionDeclaration(
                name="inspect_project",
                description="Purpose: This tool allows the AI to inspect its own project structure and file contents, which is essential for self-diagnosis, understanding the existing codebase, and verifying changes. It provides a way for the AI to dynamically explore its own environment. Results: If a path to a folder is provided, the tool returns a JSON object representing the folder's file hierarchy, including nested files and directories. If a path to a file is provided, it returns the raw content of that file as a string. Arguments: This function accepts a `path` argument, which is the relative path to the file or folder to be inspected. Use '.' to inspect the project's root directory. Restrictions/Guidelines: Use this tool when you need to understand the structure of the project or the content of a specific file. Always read the folder hierarchy first. It is a read-only tool and does not modify any files or directories. Do not use this tool to execute code or interact with external services. This tool will refuse to read files that match patterns in the project's .gitignore, with the specific exception of content within the 'logs' directory.",
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

        if os.path.isfile(path_arg):
            if self._is_ignored_file(path_arg):
                return types.Part(
                    function_response=self.function_response_error(
                        function_name,
                        f"Refused to read file '{path_arg}' as it matches a .gitignore pattern and is not explicitly allowed.",
                    )
                )

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

        abs_path = os.path.abspath(path)

        dir_structure = {
            "name": os.path.basename(abs_path),
            "path": path,
            "type": "directory",
            "children": [],
        }

        for root, dirs, files in os.walk(path, topdown=True):
            dirs[:] = [
                d
                for d in dirs
                if not self._is_ignored_file(os.path.join(root, d))
                and not d.startswith(".")
                and not (d.startswith("__") and d.endswith("__"))
            ]

            parts = os.path.relpath(root, path).split(os.sep)
            if parts[0] == ".":
                current_level = dir_structure["children"]
            else:
                node = dir_structure
                for part in parts:
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

                current_level = node.get("children", [])

            for d in sorted(dirs):
                full_path = os.path.join(root, d)
                if not self._is_ignored_file(full_path):
                    current_level.append(
                        {
                            "name": d,
                            "path": os.path.join(os.path.relpath(root, "."), d),
                            "type": "directory",
                            "children": [],
                        }
                    )

            for f in sorted(files):
                full_path = os.path.join(root, f)
                if not f.startswith(".") and not self._is_ignored_file(full_path):
                    current_level.append(
                        {
                            "name": f,
                            "path": os.path.join(os.path.relpath(root, "."), f),
                            "type": "file",
                        }
                    )

        return dir_structure
