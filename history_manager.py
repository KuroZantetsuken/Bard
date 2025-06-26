import asyncio
import base64
import json
import logging
import os
from collections import defaultdict
from config import Config
from datetime import datetime
from datetime import timedelta
from datetime import timezone
from google.genai import types
from typing import List as TypingList
from typing import NamedTuple
logger = logging.getLogger("Bard")
class HistoryEntry(NamedTuple):
    timestamp: datetime
    content: types.Content
class ChatHistoryManager:
    def __init__(self):
        self.locks = defaultdict(asyncio.Lock)
        try:
            os.makedirs(Config.HISTORY_DIR, exist_ok=True)
            logger.info(f"💾 Chat history directory created/verified: {Config.HISTORY_DIR}")
        except OSError as e:
            logger.error(f"❌ Could not create chat history directory.\nDirectory:\n{Config.HISTORY_DIR}\nError:\n{e}", exc_info=True)
    def _get_history_filepath(self, guild_id: int | None, user_id: int | None = None) -> str:
        if guild_id is not None:
            filename = f"{guild_id}.history.json"
        elif user_id is not None:
            filename = f"DM_{user_id}.history.json"
        else:
            logger.error("❌ Attempted to get history filepath with neither guild_id nor user_id.")
            filename = "unknown_history.history.json"
        return os.path.join(Config.HISTORY_DIR, filename)
    async def load_history(self, guild_id: int | None, user_id: int | None = None) -> TypingList[HistoryEntry]:
        if Config.MAX_HISTORY_TURNS == 0:
            return []
        filepath = self._get_history_filepath(guild_id, user_id)
        loaded_history_entries: TypingList[HistoryEntry] = []
        async with self.locks[filepath]:
            if not os.path.exists(filepath):
                logger.info(f"💾 No history file found. Starting fresh.\nFilepath: {filepath}")
                return []
            try:
                with open(filepath, "r", encoding="utf-8") as f:
                    raw_history_list = json.load(f)
                if not isinstance(raw_history_list, list):
                    logger.error(f"❌ History file content is not a list. Starting fresh.\nFilepath: {filepath}")
                    os.remove(filepath)
                    return []
                for item_wrapper_dict in raw_history_list:
                    if not isinstance(item_wrapper_dict, dict) or "timestamp" not in item_wrapper_dict or "content" not in item_wrapper_dict:
                        logger.warning(f"⚠️ Skipping malformed history item wrapper in {filepath}: {item_wrapper_dict}")
                        continue
                    timestamp_str = item_wrapper_dict["timestamp"]
                    item_dict = item_wrapper_dict["content"]
                    try:
                        entry_timestamp = datetime.fromisoformat(timestamp_str.replace("Z", "+00:00"))
                        if entry_timestamp.tzinfo is None:
                            entry_timestamp = entry_timestamp.replace(tzinfo=timezone.utc)
                    except ValueError:
                        logger.warning(f"⚠️ Skipping history item with invalid timestamp format in {filepath}: {timestamp_str}")
                        continue
                    loaded_parts = []
                    for part_dict in item_dict.get("parts", []):
                        if "text" in part_dict:
                            loaded_parts.append(types.Part(text=part_dict["text"]))
                        elif "inline_data" in part_dict:
                            try:
                                data_bytes = base64.b64decode(part_dict["inline_data"]["data"])
                                loaded_parts.append(types.Part(inline_data=types.Blob(
                                    mime_type=part_dict["inline_data"]["mime_type"],
                                    data=data_bytes
                                )))
                            except Exception as e_b64:
                                logger.error(f"❌ Failed to process inline_data from history.\nError:\n{e_b64}")
                                loaded_parts.append(types.Part(text="[Error: Could not load inline_data from history]"))
                        elif "file_data" in part_dict:
                             loaded_parts.append(types.Part(file_data=types.FileData(
                                mime_type=part_dict["file_data"]["mime_type"],
                                file_uri=part_dict["file_data"]["file_uri"]
                            )))
                        elif "function_call" in part_dict:
                            loaded_parts.append(types.Part(function_call=types.FunctionCall(
                                name=part_dict["function_call"]["name"],
                                args=part_dict["function_call"]["args"]
                            )))
                        elif "function_response" in part_dict:
                            loaded_parts.append(types.Part(function_response=types.FunctionResponse(
                                name=part_dict["function_response"]["name"],
                                response=part_dict["function_response"]["response"]
                            )))
                    role = item_dict.get("role", "user")
                    if role not in ("user", "model", "tool"):
                        logger.warning(f"⚠️ Invalid role found in history file: {role}. Defaulting to 'user'.")
                        role = "user"
                    reconstructed_content = types.Content(role=role, parts=loaded_parts)
                    loaded_history_entries.append(HistoryEntry(timestamp=entry_timestamp, content=reconstructed_content))
                logger.info(f"💾 Loaded {len(loaded_history_entries)} raw history entries from {filepath}.")
            except json.JSONDecodeError:
                logger.error(f"❌ Could not decode JSON from history file. Starting with fresh history for this session.\nFilepath: {filepath}")
                if os.path.exists(filepath): os.remove(filepath)
                return []
            except Exception as e:
                logger.error(f"❌ Error loading history from file.\nFilepath: {filepath}\nError:\n{e}", exc_info=True)
                return []
        if Config.MAX_HISTORY_AGE > 0:
            now_utc = datetime.now(timezone.utc)
            min_age_delta = timedelta(minutes=Config.MAX_HISTORY_AGE)
            age_filtered_entries = [
                entry for entry in loaded_history_entries
                if (now_utc - entry.timestamp) <= min_age_delta
            ]
            if len(age_filtered_entries) < len(loaded_history_entries):
                logger.info(f"💾 History filtered by age ({Config.MAX_HISTORY_AGE} min): {len(loaded_history_entries)} -> {len(age_filtered_entries)} entries.")
            loaded_history_entries = age_filtered_entries
        max_content_entries = Config.MAX_HISTORY_TURNS * 2
        if len(loaded_history_entries) > max_content_entries:
            final_history_entries = loaded_history_entries[-max_content_entries:]
            logger.info(f"💾 History truncated by turn count: {len(loaded_history_entries)} -> {len(final_history_entries)} entries (max: {max_content_entries}).")
            loaded_history_entries = final_history_entries
        return loaded_history_entries
    async def save_history(self, guild_id: int | None, user_id: int | None, history_entries: TypingList[HistoryEntry]):
        if Config.MAX_HISTORY_TURNS == 0:
            return
        filepath = self._get_history_filepath(guild_id, user_id)
        max_content_entries = Config.MAX_HISTORY_TURNS * 2
        if len(history_entries) > max_content_entries:
            entries_to_save = history_entries[-max_content_entries:]
        else:
            entries_to_save = history_entries
        logger.info(f"💾 Saving {len(entries_to_save)} history entries to {filepath}.")
        serializable_history_wrappers = []
        for entry in entries_to_save:
            content_item = entry.content
            parts_list = []
            for part in content_item.parts or []:
                if part.text is not None:
                    parts_list.append({"text": part.text})
                elif part.inline_data is not None:
                    encoded_data = base64.b64encode(part.inline_data.data if part.inline_data.data is not None else b'').decode('utf-8')
                    parts_list.append({
                        "inline_data": {
                            "mime_type": part.inline_data.mime_type,
                            "data": encoded_data
                        }
                    })
                elif part.file_data is not None:
                     parts_list.append({
                        "file_data": {
                            "mime_type": part.file_data.mime_type,
                            "file_uri": part.file_data.file_uri
                        }
                    })
                elif part.function_call is not None:
                    parts_list.append({
                        "function_call": {
                            "name": part.function_call.name,
                            "args": dict(part.function_call.args) if part.function_call.args else {}
                        }
                    })
                elif part.function_response is not None:
                     parts_list.append({
                        "function_response": {
                            "name": part.function_response.name,
                            "response": dict(part.function_response.response) if part.function_response.response else {}
                        }
                    })
            content_dict = {
                "role": content_item.role,
                "parts": parts_list
            }
            entry_wrapper_dict = {
                "timestamp": entry.timestamp.isoformat(),
                "content": content_dict
            }
            serializable_history_wrappers.append(entry_wrapper_dict)
        temp_filepath = filepath + ".tmp"
        async with self.locks[filepath]:
            try:
                with open(temp_filepath, "w", encoding="utf-8") as f:
                    json.dump(serializable_history_wrappers, f, indent=2)
                os.replace(temp_filepath, filepath)
                logger.info(f"💾 History successfully saved to {filepath}.")
            except Exception as e:
                logger.error(f"❌ Error saving history to file.\nFilepath: {filepath}\nError:\n{e}", exc_info=True)
                if os.path.exists(temp_filepath):
                    try: os.remove(temp_filepath)
                    except OSError as e_rem: logger.warning(f"⚠️ Could not remove temporary history file.\nFilepath: {temp_filepath}\nError:\n{e_rem}")
    async def delete_history(self, guild_id: int | None, user_id: int | None = None) -> bool:
        filepath = self._get_history_filepath(guild_id, user_id)
        deleted = False
        async with self.locks[filepath]:
            if os.path.exists(filepath):
                try:
                    os.remove(filepath)
                    logger.info(f"💾 Deleted history file.\nFilepath: {filepath}")
                    deleted = True
                except OSError as e:
                    logger.error(f"❌ Error deleting history file.\nFilepath: {filepath}\nError:\n{e}", exc_info=True)
            else:
                logger.info(f"💾 No history file found to delete.\nFilepath: {filepath}")
        return deleted
