import logging
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from utilities.storage import JsonStorageManager

# Initialize logger for the memory module.
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
