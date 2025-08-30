import base64
import logging
from datetime import datetime, timedelta, timezone
from typing import List, NamedTuple, Optional

from google.genai import types

from bard.util.data.storage import JsonStorageManager

logger = logging.getLogger("Bard")


class HistoryEntry(NamedTuple):
    """Represents a single entry in the chat history."""

    timestamp: datetime
    content: types.Content


class ChatHistoryManager(JsonStorageManager):
    """
    Manages chat history by loading, saving, and filtering chat entries.
    History is stored as JSON files, typically per user or guild.
    """

    def __init__(self, history_dir: str, max_history_turns: int, max_history_age: int):
        """
        Initializes the ChatHistoryManager.

        Args:
            history_dir: The directory where chat history files are stored.
            max_history_turns: The maximum number of conversational turns to retain.
            max_history_age: The maximum age (in minutes) for history entries to be considered valid.
        """
        super().__init__(storage_dir=history_dir, file_suffix=".history.json")
        self.max_history_turns = max_history_turns
        self.max_history_age = max_history_age

    async def load_history(
        self, guild_id: Optional[int], user_id: Optional[str]
    ) -> List[HistoryEntry]:
        """
        Loads chat history for a specific guild and user, applying age and turn count filters.

        Args:
            guild_id: The ID of the Discord guild, if applicable.
            user_id: The ID of the user.

        Returns:
            A list of filtered HistoryEntry objects.
        """
        if self.max_history_turns == 0:
            return []

        raw_history = await self._load_data(guild_id, user_id)
        if not raw_history:
            return []

        loaded_entries: List[HistoryEntry] = []
        for entry in raw_history:
            try:
                timestamp = datetime.fromisoformat(
                    entry["timestamp"].replace("Z", "+00:00")
                )
                if timestamp.tzinfo is None:
                    timestamp = timestamp.replace(tzinfo=timezone.utc)

                content_data = entry.get("content", {})
                parts = []

                for part_data in content_data.get("parts", []):
                    part = self._deserialize_part(part_data)
                    if part:
                        parts.append(part)

                role = content_data.get("role", "user")
                if role not in ("user", "model", "tool"):
                    role = "user"

                loaded_entries.append(
                    HistoryEntry(
                        timestamp=timestamp,
                        content=types.Content(role=role, parts=parts),
                    )
                )
            except Exception as e:
                logger.error(f"Error parsing history entry: {e}", exc_info=True)

        return self._filter_entries(loaded_entries)

    def _deserialize_part(self, part_data: dict) -> Optional[types.Part]:
        """
        Deserializes a dictionary representation of a content part into a Gemini types.Part object.
        Supports text, inline data, file data, function call, and function response parts.
        """
        if "text" in part_data:
            return types.Part(text=part_data["text"])
        elif "inline_data" in part_data:
            data_str = part_data["inline_data"].get("data", "")
            data_bytes = base64.b64decode(data_str) if data_str else b""
            return types.Part(
                inline_data=types.Blob(
                    mime_type=part_data["inline_data"].get(
                        "mime_type", "application/octet-stream"
                    ),
                    data=data_bytes,
                )
            )
        elif "file_data" in part_data:
            return types.Part(
                file_data=types.FileData(
                    mime_type=part_data["file_data"].get(
                        "mime_type", "application/octet-stream"
                    ),
                    file_uri=part_data["file_data"].get("file_uri", ""),
                )
            )
        elif "function_call" in part_data:
            return types.Part(
                function_call=types.FunctionCall(
                    name=part_data["function_call"].get("name", ""),
                    args=part_data["function_call"].get("args", {}),
                )
            )
        elif "function_response" in part_data:
            return types.Part(
                function_response=types.FunctionResponse(
                    name=part_data["function_response"].get("name", ""),
                    response=part_data["function_response"].get("response", {}),
                )
            )
        return None

    def _filter_entries(self, entries: List[HistoryEntry]) -> List[HistoryEntry]:
        """
        Filters a list of history entries based on age and maximum turn count.
        Entries older than `max_history_age` are removed.
        If the number of turns exceeds `max_history_turns`, the oldest turns are truncated.
        """
        if self.max_history_age > 0:
            now = datetime.now(timezone.utc)
            cutoff = now - timedelta(minutes=self.max_history_age)
            entries = [e for e in entries if e.timestamp >= cutoff]

        if self.max_history_turns > 0 and len(entries) > self.max_history_turns * 2:
            entries = entries[-(self.max_history_turns * 2) :]

        return entries

    async def save_history(
        self,
        guild_id: Optional[int],
        user_id: Optional[str],
        entries: List[HistoryEntry],
    ) -> None:
        """
        Saves chat history for a specific guild and user after applying truncation.

        Args:
            guild_id: The ID of the Discord guild, if applicable.
            user_id: The ID of the user.
            entries: The list of HistoryEntry objects to save.
        """
        if self.max_history_turns == 0:
            return

        entries_to_save = self._filter_entries(entries)
        serializable = [self._serialize_entry(e) for e in entries_to_save]
        await self._save_data(guild_id, user_id, serializable)

    def _serialize_entry(self, entry: HistoryEntry) -> dict:
        """
        Serializes a HistoryEntry object into a dictionary for JSON storage.
        Includes timestamp and serialized content parts.
        """
        parts = [self._serialize_part(part) for part in (entry.content.parts or [])]
        return {
            "timestamp": entry.timestamp.isoformat(),
            "content": {"role": entry.content.role, "parts": parts},
        }

    def _serialize_part(self, part: types.Part) -> dict:
        """
        Serializes a Gemini types.Part object into a dictionary.
        Handles different types of parts: text, inline data, file data, function call, and function response.
        """
        part_dict = {}
        if part.text is not None:
            part_dict["text"] = part.text
        elif part.inline_data is not None:
            data = part.inline_data.data or b""
            part_dict["inline_data"] = {
                "mime_type": part.inline_data.mime_type or "application/octet-stream",
                "data": base64.b64encode(data).decode("utf-8"),
            }
        elif part.file_data is not None:
            part_dict["file_data"] = {
                "mime_type": part.file_data.mime_type or "application/octet-stream",
                "file_uri": part.file_data.file_uri or "",
            }
        elif part.function_call is not None:
            part_dict["function_call"] = {
                "name": part.function_call.name or "",
                "args": dict(part.function_call.args or {}),
            }
        elif part.function_response is not None:
            part_dict["function_response"] = {
                "name": part.function_response.name or "",
                "response": dict(part.function_response.response or {}),
            }
        return part_dict

    async def delete_history(
        self, guild_id: Optional[int], user_id: Optional[str]
    ) -> bool:
        """
        Deletes the chat history file for a specific guild and user.

        Args:
            guild_id: The ID of the Discord guild, if applicable.
            user_id: The ID of the user.

        Returns:
            True if the history file was successfully deleted, False otherwise.
        """
        return await self._delete_data(guild_id, user_id)
