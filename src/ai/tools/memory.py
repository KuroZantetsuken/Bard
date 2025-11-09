import asyncio
import json
import logging
import os
from collections import defaultdict
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from google.genai import types

from ai.tools.base import BaseTool, ToolContext
from settings import Settings

log = logging.getLogger("Bard")


class JsonStorageManager:
    """
    A base class for managing data stored in JSON files.
    It provides methods for loading, saving, and deleting JSON data,
    with support for asynchronous operations and file locking to prevent corruption.
    """

    def __init__(self, storage_dir: str, file_suffix: str):
        """
        Initializes the JsonStorageManager.

        Args:
            storage_dir: The base directory where JSON files will be stored.
            file_suffix: The suffix to append to filenames (e.g., ".memory.json").
        """
        log.debug(
            "Initializing JsonStorageManager",
            extra={"storage_dir": storage_dir, "file_suffix": file_suffix},
        )
        self.storage_dir = storage_dir
        self.file_suffix = file_suffix
        self.storage_locks = defaultdict(asyncio.Lock)
        try:
            os.makedirs(self.storage_dir, exist_ok=True)
        except OSError as e:
            log.error(
                f"Could not create storage directory '{self.storage_dir}'. Error: {e}",
                exc_info=True,
            )

    def _get_storage_filepath(
        self, guild_id: Optional[int], user_id: Optional[str]
    ) -> str:
        """
        Constructs the full file path for a storage file based on guild ID or user ID.

        Args:
            guild_id: The ID of the Discord guild, if applicable.
            user_id: The ID of the user, if applicable (for DMs or user-specific storage).

        Returns:
            The complete file path for the JSON storage file.
        """
        if guild_id is not None:
            base_name = str(guild_id)
        elif user_id is not None:
            base_name = str(user_id)
        else:
            log.error(
                "Attempted to get storage filepath with neither guild_id nor user_id"
            )
            base_name = "unknown"

        filename = f"{base_name}{self.file_suffix}"
        return os.path.join(self.storage_dir, filename)

    async def _load_data(
        self, guild_id: Optional[int], user_id: Optional[str]
    ) -> List[Dict[str, Any]]:
        """
        Loads data from a JSON file.

        Args:
            guild_id: The ID of the Discord guild.
            user_id: The ID of the user.

        Returns:
            A list of dictionaries representing the loaded JSON data, or an empty list if
            the file does not exist or an error occurs during loading/parsing.
        """
        filepath = self._get_storage_filepath(guild_id, user_id)
        log.debug(f"Loading data from {filepath}")
        async with self.storage_locks[filepath]:
            if not os.path.exists(filepath):
                return []
            try:
                with open(filepath, "r", encoding="utf-8") as f:
                    data = json.load(f)
                if not isinstance(data, list):
                    log.error(
                        f"Invalid data format (not a list) for {filepath}. Deleting file."
                    )
                    try:
                        os.remove(filepath)
                    except OSError as remove_error:
                        log.error(f"Error deleting corrupt data file: {remove_error}")
                    return []
                return data
            except (json.JSONDecodeError, IOError) as e:
                log.error(f"Error loading data from {filepath}: {e}", exc_info=True)
                return []

    async def _save_data(
        self,
        guild_id: Optional[int],
        user_id: Optional[str],
        data: List[Dict[str, Any]],
    ) -> None:
        """
        Saves data to a JSON file. It uses a temporary file and atomic rename
        to prevent data corruption during writes.

        Args:
            guild_id: The ID of the Discord guild.
            user_id: The ID of the user.
            data: The list of dictionaries to save as JSON.
        """
        filepath = self._get_storage_filepath(guild_id, user_id)
        log.debug(f"Saving data to {filepath}")
        temp_path = f"{filepath}.tmp"
        async with self.storage_locks[filepath]:
            try:
                with open(temp_path, "w", encoding="utf-8") as f:
                    json.dump(data, f, indent=2)
                os.replace(temp_path, filepath)
            except Exception as e:
                log.error(f"Error saving data to {filepath}: {e}", exc_info=True)
                if os.path.exists(temp_path):
                    try:
                        os.remove(temp_path)
                    except OSError as remove_error:
                        log.error(f"Error removing temporary file: {remove_error}")

    async def _delete_data(
        self, guild_id: Optional[int], user_id: Optional[str]
    ) -> bool:
        """
        Deletes a data file.

        Args:
            guild_id: The ID of the Discord guild.
            user_id: The ID of the user.

        Returns:
            True if the file was successfully deleted, False otherwise.
        """
        filepath = self._get_storage_filepath(guild_id, user_id)
        log.debug(f"Deleting data file: {filepath}")
        async with self.storage_locks[filepath]:
            if os.path.exists(filepath):
                try:
                    os.remove(filepath)
                    log.info(f"Successfully deleted data file: {filepath}")
                    return True
                except OSError as e:
                    log.error(
                        f"Error deleting data file {filepath}: {e}", exc_info=True
                    )
            return False


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
        log.debug(
            "Initializing MemoryManager",
            extra={"memory_dir": memory_dir, "max_memories": max_memories},
        )
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
        log.debug(f"Loading memories for user {user_id}")
        memories = await self._load_data(guild_id=None, user_id=user_id)
        if not isinstance(memories, list):
            log.error(f"Invalid memories format for user {user_id}")
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
            log.warning(f"Filtered invalid memories for user {user_id}")

        memories = valid_memories
        if self.max_memories > 0 and len(memories) > self.max_memories:
            log.debug(f"Truncating memories for user {user_id} to {self.max_memories}")
            memories = memories[-self.max_memories :]

        return memories

    async def save_memories(self, user_id: str, memories: List[Dict[str, Any]]) -> None:
        """
        Saves memories for a specific user, applying truncation based on `max_memories`.
        Args:
            user_id: The ID of the user.
            memories: The list of memory dictionaries to save.
        """
        log.debug(f"Saving memories for user {user_id}")
        if self.max_memories > 0 and len(memories) > self.max_memories:
            log.debug(f"Truncating memories for user {user_id} to {self.max_memories}")
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
        log.debug(f"Adding memory for user {user_id}")
        content = memory_content.strip()
        if not (5 <= len(content) <= 1000):
            log.warning(f"Memory content length out of bounds: {len(content)} chars")
            return False

        memories = await self.load_memories(user_id)
        new_id = self._next_memory_id(memories)
        new_memory = {
            "id": new_id,
            "content": content,
            "timestamp_added": datetime.now(timezone.utc).isoformat(),
        }
        memories.append(new_memory)
        await self.save_memories(user_id, memories)
        log.info(f"Added memory {new_id} for user {user_id}")
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
        log.debug(f"Removing memory {memory_id} for user {user_id}")
        memories = await self.load_memories(user_id)
        initial_count = len(memories)
        memories = [m for m in memories if m.get("id") != memory_id]
        if len(memories) < initial_count:
            await self.save_memories(user_id, memories)
            log.info(f"Removed memory {memory_id} for user {user_id}")
            return True
        log.warning(f"Memory {memory_id} not found for user {user_id}")
        return False

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
            memory_dir=Settings.MEMORY_DIR, max_memories=Settings.MAX_MEMORIES
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
        log.info(f"Executing tool '{function_name}'")
        log.debug("Tool arguments", extra={"tool_args": args})
        user_id = context.get("user_id")
        if user_id is None:
            log.error("User ID not found in context for memory operation.")
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
                log.warning("add_user_memory called without 'memory_content'.")
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
                log.warning("remove_user_memory called without 'memory_id'.")
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
                log.warning(f"Invalid 'memory_id' provided: {id_arg}.")
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
            log.error(f"Unknown function '{function_name}' called in MemoryTool.")
            return types.Part(
                function_response=types.FunctionResponse(
                    name=function_name,
                    response={
                        "success": False,
                        "error": f"Unknown function in MemoryTool: {function_name}",
                    },
                )
            )
