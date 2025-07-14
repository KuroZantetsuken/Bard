import logging
from typing import Any, Dict, List

from google.genai import types

from tools.base import BaseTool, ToolContext

# Initialize logger for the memory tool module.
logger = logging.getLogger("Bard")


class MemoryTool(BaseTool):
    """
    A tool for managing user-specific long-term memories.
    It allows the AI to add and remove memories to enhance personalized interactions.
    """

    tool_emoji = "🧠"

    def __init__(self, context: ToolContext):
        """
        Initializes the MemoryTool.

        Args:
            context: The ToolContext object providing shared resources.
        """
        super().__init__(context=context)

    def get_function_declarations(self) -> List[types.FunctionDeclaration]:
        """
        Returns the function declarations for the `add_user_memory` and `remove_user_memory` functions.
        These functions are exposed to the Gemini model to allow it to manage user memories.
        """
        return [
            types.FunctionDeclaration(
                name="add_user_memory",
                description=(
                    'Purpose: This tool is designed to establish and maintain long-term, user-specific memory for the AI. It allows the AI to persistently retain and recall important facts, stated preferences, or other contextual details about a user across various sessions and conversations, enhancing the personalized interaction experience. Results: While the underlying function returns a success or failure status indicating whether the memory was successfully added, the AI should interpret this outcome to formulate an appropriate conversational response to the user. For example, the AI should confirm the successful addition of the memory or inform the user if there was an issue. Restrictions/Guidelines: This tool should be invoked exclusively when a user explicitly asks the AI to remember something (e.g., "Remember that my favorite color is blue"), or when a user\'s statement clearly implies a piece of information that would be genuinely beneficial for the AI to recall in subsequent interactions. It is crucial to avoid using this tool for transient conversational context or information that is not intended for long-term retention.'
                ),
                parameters=types.Schema(
                    type=types.Type.OBJECT,
                    properties={
                        "memory_content": types.Schema(
                            type=types.Type.STRING,
                            description="The textual content of the memory to be saved for the user.",
                        ),
                    },
                    required=["memory_content"],
                ),
            ),
            types.FunctionDeclaration(
                name="remove_user_memory",
                description=(
                    "Purpose: This tool plays a critical role in managing and curating the user's long-term memory by enabling the AI to remove outdated, incorrect, or no longer relevant information. This ensures the AI's memory remains accurate, efficient, and aligned with the user's current preferences. Results: Similar to `add_user_memory`, while the function itself provides a success or failure status, the AI should use this to inform its conversational response. The AI should confirm the successful removal of the memory or explain if the removal could not be completed. Restrictions/Guidelines: This tool should be used when the user explicitly requests the AI to forget a specific piece of information (e.g., \"Forget what I said about my address\"), or when new information provided by the user directly contradicts or invalidates a previously stored memory. The AI should prioritize accuracy and user preference in all memory management operations."
                ),
                parameters=types.Schema(
                    type=types.Type.OBJECT,
                    properties={
                        "memory_id": types.Schema(
                            type=types.Type.INTEGER,
                            description="The unique numerical identifier of the memory to be removed. Infer this from the memories listed in your context.",
                        ),
                    },
                    required=["memory_id"],
                ),
            ),
        ]

    async def execute_tool(
        self, function_name: str, args: Dict[str, Any], context: ToolContext
    ) -> types.Part:
        """
        Executes a specified memory management function (`add_user_memory` or `remove_user_memory`).

        Args:
            function_name: The name of the function to execute.
            args: A dictionary of arguments for the function.
            context: The ToolContext object providing shared resources, including the memory service.

        Returns:
            A Gemini types.Part object containing the function response, including
            success status and details of the operation.
        """
        user_id = context.get("user_id")
        if user_id is None:
            logger.error("User ID not found in context for memory operation.")
            return types.Part(
                function_response=types.FunctionResponse(
                    name=function_name,
                    response={
                        "success": False,
                        "error": "User ID not available in context for memory operation.",
                    },
                )
            )
        memory_service = context.memory_service
        if memory_service is None:
            logger.error("Memory service not found in context for memory operation.")
            return types.Part(
                function_response=types.FunctionResponse(
                    name=function_name,
                    response={
                        "success": False,
                        "error": "Memory service not available in context.",
                    },
                )
            )
        if function_name == "add_user_memory":
            content_arg = args.get("memory_content")
            if content_arg:
                success = await memory_service.add_memory(user_id, content_arg)
                return types.Part(
                    function_response=types.FunctionResponse(
                        name=function_name,
                        response={
                            "success": success,
                            "action": "added",
                            "preview": content_arg[:30] + "..." if content_arg else "",
                        },
                    )
                )
            else:
                logger.warning("add_user_memory called without 'memory_content'.")
                return types.Part(
                    function_response=types.FunctionResponse(
                        name=function_name,
                        response={
                            "success": False,
                            "error": "Missing memory_content argument.",
                        },
                    )
                )
        elif function_name == "remove_user_memory":
            id_arg = args.get("memory_id")
            if id_arg is None:
                logger.warning("remove_user_memory called without 'memory_id'.")
                return types.Part(
                    function_response=types.FunctionResponse(
                        name=function_name,
                        response={
                            "success": False,
                            "error": "Missing memory_id argument.",
                        },
                    )
                )
            try:
                mem_id = int(id_arg)
                success = await memory_service.remove_memory(user_id, mem_id)
                return types.Part(
                    function_response=types.FunctionResponse(
                        name=function_name,
                        response={
                            "success": success,
                            "action": "removed",
                            "id": mem_id,
                        },
                    )
                )
            except (ValueError, TypeError):
                logger.warning(f"Invalid 'memory_id' provided: {id_arg}.")
                return types.Part(
                    function_response=types.FunctionResponse(
                        name=function_name,
                        response={
                            "success": False,
                            "error": f"Invalid memory_id: {id_arg}. Must be an integer.",
                        },
                    )
                )
        else:
            logger.error(f"Unknown function '{function_name}' called in MemoryTool.")
            return types.Part(
                function_response=types.FunctionResponse(
                    name=function_name,
                    response={
                        "success": False,
                        "error": f"Unknown function in MemoryTool: {function_name}",
                    },
                )
            )
