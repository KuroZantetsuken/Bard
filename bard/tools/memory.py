import logging
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from google.genai import types

from bard.tools.base import BaseTool, ToolContext
from bard.util.data.storage import JsonStorageManager
from config import Config

logger = logging.getLogger("Bard")


class MemoryManager(JsonStorageManager):
    """
    Manages user-specific long-term memories using JSON file storage.
    Provides functionality to add, remove, load, and save memories.
    """

    def __init__(self, memory_dir: str, max_memories: int):
        """
        Initializes the MemoryManager.

        Args:
            memory_dir: The directory where memory files are stored.
            max_memories: The maximum number of memories to store per user.
        """
        super().__init__(storage_dir=memory_dir, file_suffix=".memory.json")
        self.max_memories = max_memories

    def _next_memory_id(self, memories: List[Dict[str, Any]]) -> int:
        """
        Generates the next available memory ID.
        Args:
            memories: A list of existing memory dictionaries.
        Returns:
            The next integer ID for a new memory.
        """
        return max((m.get("id", 0) for m in memories), default=0) + 1

    async def load_memories(self, user_id: str) -> List[Dict[str, Any]]:
        """
        Loads memories for a specific user, with truncation based on `max_memories`.
        Args:
            user_id: The ID of the user.
        Returns:
            A list of memory dictionaries.
        """
        memories = await self._load_data(guild_id=None, user_id=user_id)
        if not isinstance(memories, list):
            logger.error(f"Invalid memories format for user {user_id}")
            return []

        valid_memories = [
            m
            for m in memories
            if isinstance(m, dict)
            and "id" in m
            and "content" in m
            and "timestamp_added" in m
        ]
        if len(valid_memories) != len(memories):
            logger.warning(f"Filtered invalid memories for user {user_id}")

        memories = valid_memories
        if self.max_memories > 0 and len(memories) > self.max_memories:
            memories = memories[-self.max_memories :]

        return memories

    async def save_memories(self, user_id: str, memories: List[Dict[str, Any]]) -> None:
        """
        Saves memories for a specific user, applying truncation based on `max_memories`.
        Args:
            user_id: The ID of the user.
            memories: The list of memory dictionaries to save.
        """
        if self.max_memories > 0 and len(memories) > self.max_memories:
            memories = memories[-self.max_memories :]
        await self._save_data(guild_id=None, user_id=user_id, data=memories)

    async def add_memory(self, user_id: str, memory_content: str) -> bool:
        """
        Adds a new memory for a user with content validation.
        Args:
            user_id: The ID of the user.
            memory_content: The text content of the memory.
        Returns:
            True if the memory was added successfully, False otherwise.
        """
        content = memory_content.strip()
        if not (5 <= len(content) <= 1000):
            logger.warning(f"Memory content length out of bounds: {len(content)} chars")
            return False

        memories = await self.load_memories(user_id)
        new_id = self._next_memory_id(memories)
        memories.append(
            {
                "id": new_id,
                "content": content,
                "timestamp_added": datetime.now(timezone.utc).isoformat(),
            }
        )
        await self.save_memories(user_id, memories)
        return True

    async def remove_memory(self, user_id: str, memory_id: int) -> bool:
        """
        Remove a specific memory by ID.
        Args:
            user_id: The ID of the user.
            memory_id: The ID of the memory to remove.
        Returns:
            True if the memory was removed successfully, False otherwise.
        """
        memories = await self.load_memories(user_id)
        initial_count = len(memories)
        memories = [m for m in memories if m.get("id") != memory_id]
        if len(memories) < initial_count:
            await self.save_memories(user_id, memories)
            return True
        return False

    async def delete_all_memories(self, user_id: str) -> bool:
        """
        Delete all memories for a user.
        Args:
            user_id: The ID of the user.
        Returns:
            True if all memories were deleted, False otherwise.
        """
        return await self._delete_data(guild_id=None, user_id=user_id)

    def format_memories(
        self, user_id: Optional[str], memories: List[Dict[str, Any]]
    ) -> str:
        """
        Format memories for LLM context.
        Args:
            user_id: The ID of the user.
            memories: A list of memory dictionaries.
        Returns:
            A formatted string of memories.
        """
        if not memories or user_id is None:
            return ""
        formatted = [f"[{user_id}:MEMORY:START]"]
        for mem in memories:
            formatted.append(f"ID: `{mem.get('id')}`")
            formatted.append(f"Recorded: `{mem.get('timestamp_added')}`")
            formatted.append(mem.get("content", "[Empty memory content]"))
        formatted.append(f"[{user_id}:MEMORY:END]")
        return "\n".join(formatted)

    async def get_memory(self, user_id: str, memory_key: str) -> Optional[Any]:
        """
        Retrieves a specific memory for a user by content key.
        Args:
            user_id: The ID of the user.
            memory_key: The content string to match for the memory.
        Returns:
            The content of the matching memory, or None if not found or an error occurs.
        """
        try:
            memories = await self.load_memories(user_id)
            for mem in memories:
                if mem.get("content") == memory_key:
                    return mem.get("content")
        except Exception as e:
            logger.error(
                f"Error in get_memory for user {user_id}, key {memory_key}: {e}",
                exc_info=True,
            )
        return None

    async def set_memory(
        self, user_id: str, memory_key: str, memory_value: Any
    ) -> None:
        """
        Sets or updates a specific memory for a user by content key.
        Args:
            user_id: The ID of the user.
            memory_key: The content string to identify the memory.
            memory_value: The new value for the memory content.
        """
        try:
            memories = await self.load_memories(user_id)
            found = False
            for mem in memories:
                if mem.get("content") == memory_key:
                    mem["content"] = memory_value
                    mem["timestamp_added"] = datetime.now(timezone.utc).isoformat()
                    found = True
                    break
            if not found:
                new_id = self._next_memory_id(memories)
                memories.append(
                    {
                        "id": new_id,
                        "content": memory_value,
                        "timestamp_added": datetime.now(timezone.utc).isoformat(),
                    }
                )
            await self.save_memories(user_id, memories)
        except Exception as e:
            logger.error(
                f"Error in set_memory for user {user_id}, key {memory_key}: {e}",
                exc_info=True,
            )


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

        self.memory_manager = MemoryManager(
            memory_dir=Config.MEMORY_DIR, max_memories=Config.MAX_MEMORIES
        )

    def get_function_declarations(self) -> List[types.FunctionDeclaration]:
        """
        Returns the function declarations for the `add_user_memory` and `remove_user_memory` functions.
        These functions are exposed to the Gemini model to allow it to manage user memories.
        """
        return [
            types.FunctionDeclaration(
                name="add_user_memory",
                description=(
                    "Purpose: This tool is designed to establish and maintain long-term, user-specific memory for the AI. It allows the AI to persistently retain and recall important facts, stated preferences, or other contextual details about a user across various sessions and conversations, enhancing the personalized interaction experience. Results: While the underlying function returns a success or failure status indicating whether the memory was successfully added, the AI should interpret this outcome to formulate an appropriate conversational response to the user. For example, the AI should confirm the successful addition of the memory or inform the user if there was an issue. Restrictions/Guidelines: This tool should be invoked exclusively when a user explicitly asks the AI to remember something (e.g., \"Remember that my favorite color is blue\"), or when a user's statement clearly implies a piece of information that would be genuinely beneficial for the AI to recall in subsequent interactions. It is crucial to avoid using this tool for transient conversational context or information that is not intended for long-term retention. Memories are saved to a file named with the user's ID, making the subject implicit; therefore, you should omit the subject from the saved memory content. If a user provides multiple distinct pieces of information, call the memory tool for each one individually."
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

        memory_service = self.memory_manager
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
