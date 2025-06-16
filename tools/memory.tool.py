import asyncio
import json
import logging
import os
from collections import defaultdict
from config import Config
from datetime import datetime
from datetime import timezone
from google.genai import types
from tools import BaseTool
from tools import ToolContext
from typing import Any
from typing import Dict
from typing import List as TypingList
logger = logging.getLogger("Bard")
class MemoryManager:
    """Manages persistent user-specific memories."""
    def __init__(self, config: Config):
        self.config = config
        self.locks = defaultdict(asyncio.Lock)
        try:
            os.makedirs(self.config.MEMORY_DIR, exist_ok=True)
            logger.info(f"🧠 Memory directory created/verified: {self.config.MEMORY_DIR}")
        except OSError as e:
            logger.error(f"❌ Could not create memory directory.\nDirectory:\n{self.config.MEMORY_DIR}\nError:\n{e}", exc_info=True)
    def _get_memory_filepath(self, user_id: int) -> str:
        filename = f"{user_id}.memory.json"
        return os.path.join(self.config.MEMORY_DIR, filename)
    def _generate_memory_id(self, existing_memories: TypingList[Dict[str, Any]]) -> int:
        if not existing_memories:
            return 1
        return max(item.get("id", 0) for item in existing_memories) + 1
    async def load_memories(self, user_id: int) -> TypingList[Dict[str, Any]]:
        filepath = self._get_memory_filepath(user_id)
        memories_list: TypingList[Dict[str, Any]] = []
        async with self.locks[filepath]:
            if not os.path.exists(filepath):
                return []
            try:
                with open(filepath, "r", encoding="utf-8") as f:
                    loaded_data = json.load(f)
                if isinstance(loaded_data, list):
                    memories_list = [
                        item for item in loaded_data
                        if isinstance(item, dict) and
                           "id" in item and
                           "content" in item and
                           "timestamp_added" in item
                    ]
                    if len(memories_list) != len(loaded_data):
                        logger.warning(f"🧠 Some malformed memory items were filtered for user {user_id}.")
                else:
                    logger.error(f"❌ Memory file for user {user_id} is not a list. Discarding.\nFilepath: {filepath}")
                    if os.path.exists(filepath): os.remove(filepath)
                    return []
            except json.JSONDecodeError:
                logger.error(f"❌ Could not decode JSON from memory file. Discarding.\nUser ID: {user_id}\nFilepath: {filepath}")
                if os.path.exists(filepath): os.remove(filepath)
                return []
            except Exception as e:
                logger.error(f"❌ Error loading memories from file.\nUser ID: {user_id}\nFilepath: {filepath}\nError:\n{e}", exc_info=True)
                return []
        if len(memories_list) > self.config.MAX_MEMORIES:
            memories_list = memories_list[-self.config.MAX_MEMORIES:]
            logger.info(f"🧠 Memories for user {user_id} truncated to {len(memories_list)} entries (max: {self.config.MAX_MEMORIES}).")
        return memories_list
    async def save_memories(self, user_id: int, memories: TypingList[Dict[str, Any]]):
        filepath = self._get_memory_filepath(user_id)
        if len(memories) > self.config.MAX_MEMORIES:
            memories_to_save = memories[-self.config.MAX_MEMORIES:]
        else:
            memories_to_save = memories
        temp_filepath = filepath + ".tmp"
        async with self.locks[filepath]:
            try:
                with open(temp_filepath, "w", encoding="utf-8") as f:
                    json.dump(memories_to_save, f, indent=2)
                os.replace(temp_filepath, filepath)
            except Exception as e:
                logger.error(f"❌ Error saving memories to file.\nUser ID: {user_id}\nFilepath: {filepath}\nError:\n{e}", exc_info=True)
                if os.path.exists(temp_filepath):
                    try: os.remove(temp_filepath)
                    except OSError as e_rem: logger.warning(f"⚠️ Could not remove temporary memory file.\nFilepath: {temp_filepath}\nError:\n{e_rem}")
    async def add_memory(self, user_id: int, memory_content: str) -> bool:
        if not memory_content.strip():
            logger.warning(f"🧠 Attempted to add empty memory for user {user_id}. Skipping.")
            return False
        memories = await self.load_memories(user_id)
        new_id = self._generate_memory_id(memories)
        timestamp = datetime.now(timezone.utc).isoformat()
        new_memory = {"id": new_id, "content": memory_content.strip(), "timestamp_added": timestamp}
        memories.append(new_memory)
        await self.save_memories(user_id, memories)
        logger.info(f"🧠 Added memory ID {new_id} for user {user_id}: '{memory_content.strip()[:50]}...'")
        return True
    async def remove_memory(self, user_id: int, memory_id_to_remove: int) -> bool:
        memories = await self.load_memories(user_id)
        initial_count = len(memories)
        memories_after_removal = [mem for mem in memories if mem.get("id") != memory_id_to_remove]
        if len(memories_after_removal) < initial_count:
            await self.save_memories(user_id, memories_after_removal)
            logger.info(f"🧠 Removed memory ID {memory_id_to_remove} for user {user_id}.")
            return True
        else:
            logger.warning(f"🧠 Memory ID {memory_id_to_remove} not found for user {user_id}. No removal performed.")
            return False
    async def delete_all_memories(self, user_id: int) -> bool:
        filepath = self._get_memory_filepath(user_id)
        deleted = False
        async with self.locks[filepath]:
            if os.path.exists(filepath):
                try:
                    os.remove(filepath)
                    logger.info(f"🧠 Deleted all memories for user {user_id}. File: {filepath}")
                    deleted = True
                except OSError as e:
                    logger.error(f"❌ Error deleting memory file for user {user_id}.\nFilepath: {filepath}\nError:\n{e}", exc_info=True)
            else:
                logger.info(f"🧠 No memory file found to delete for user {user_id}.\nFilepath: {filepath}")
        return deleted
    def format_memories_for_llm_prompt(self, user_id: int, memories: TypingList[Dict[str, Any]]) -> str:
        if not memories:
            return ""
        formatted_mem_parts = [f"[{user_id}:MEMORY:START]"]
        for mem in memories:
            formatted_mem_parts.append(f"ID: `{mem.get('id')}`")
            formatted_mem_parts.append(f"Recorded: `{mem.get('timestamp_added')}`")
            formatted_mem_parts.append(mem.get('content', '[Error: Memory content missing]'))
        formatted_mem_parts.append(f"[{user_id}:MEMORY:END]")
        return "\n".join(formatted_mem_parts)
class MemoryTool(BaseTool):
    def __init__(self, config: Config):
        self.config = config
        self.memory_manager = MemoryManager(config)
    def get_function_declarations(self) -> TypingList[types.FunctionDeclaration]:
        return [
            types.FunctionDeclaration(
                name="add_user_memory",
                description=(
                    "Stores a piece of information (memory) about the user that they have stated or implied. "
                    "After calling this, formulate a chat response acknowledging the memory was saved."
                ),
                parameters=types.Schema(
                    type=types.Type.OBJECT,
                    properties={
                        "memory_content": types.Schema(
                            type=types.Type.STRING,
                            description="The textual content of the memory to be saved for the user."
                        ),
                    },
                    required=["memory_content"],
                )
            ),
            types.FunctionDeclaration(
                name="remove_user_memory",
                description=(
                    "Removes a previously stored memory for the user, identified by its ID. Memory IDs are provided when listing memories "
                    "or can be inferred from context. After calling this, formulate a chat response acknowledging the memory was removed."
                ),
                parameters=types.Schema(
                    type=types.Type.OBJECT,
                    properties={
                        "memory_id": types.Schema(
                            type=types.Type.INTEGER,
                            description="The unique numerical identifier of the memory to be removed."
                        ),
                    },
                    required=["memory_id"],
                )
            )
        ]
    async def execute_tool(self, function_name: str, args: Dict[str, Any], context: ToolContext) -> types.Part:
        user_id = context.get("user_id")
        if user_id is None:
            logger.error("❌ MemoryTool: user_id not found in context.")
            return types.Part(function_response=types.FunctionResponse(
                name=function_name,
                response={"success": False, "error": "User ID not available in context for memory operation."}
            ))
        if function_name == "add_user_memory":
            content_arg = args.get("memory_content")
            if content_arg:
                success = await self.memory_manager.add_memory(user_id, content_arg)
                return types.Part(function_response=types.FunctionResponse(
                    name=function_name,
                    response={"success": success, "action": "added", "preview": content_arg[:30]+"..." if content_arg else ""}
                ))
            else:
                logger.warning("🧠 'add_user_memory' called without 'memory_content'.")
                return types.Part(function_response=types.FunctionResponse(
                    name=function_name,
                    response={"success": False, "error": "Missing memory_content argument."}
                ))
        elif function_name == "remove_user_memory":
            id_arg = args.get("memory_id")
            try:
                mem_id = int(id_arg)
                success = await self.memory_manager.remove_memory(user_id, mem_id)
                return types.Part(function_response=types.FunctionResponse(
                    name=function_name,
                    response={"success": success, "action": "removed", "id": mem_id}
                ))
            except (ValueError, TypeError):
                logger.warning(f"🧠 'remove_user_memory' called with invalid 'memory_id': {id_arg}.")
                return types.Part(function_response=types.FunctionResponse(
                    name=function_name,
                    response={"success": False, "error": f"Invalid memory_id: {id_arg}. Must be an integer."}
                ))
        else:
            logger.error(f"❌ MemoryTool: Unknown function_name '{function_name}'")
            return types.Part(function_response=types.FunctionResponse(
                name=function_name,
                response={"success": False, "error": f"Unknown function in MemoryTool: {function_name}"}
            ))