import logging
from datetime import datetime, timedelta, timezone
from typing import List, NamedTuple

from google.genai import types

logger = logging.getLogger("Bard")


class HistoryEntry(NamedTuple):
    """Represents a single entry in the chat history."""

    timestamp: datetime
    content: types.Content


class ChatHistoryManager:
    """
    Manages chat history in-memory, applying age and turn count filters.
    """

    def __init__(self, max_history_turns: int, max_history_age: int):
        """
        Initializes the ChatHistoryManager.

        Args:
            max_history_turns: The maximum number of conversational turns to retain.
            max_history_age: The maximum age (in minutes) for history entries to be considered valid.
        """
        self.max_history_turns = max_history_turns
        self.max_history_age = max_history_age
        self._history: List[HistoryEntry] = []

    def load_history(self) -> List[HistoryEntry]:
        """
        Loads chat history, applying age and turn count filters.
        Since history is now transient, this method just returns the current in-memory history.

        Returns:
            A list of filtered HistoryEntry objects.
        """
        return self._filter_entries(self._history)

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

    def save_history(self, entries: List[HistoryEntry]) -> None:
        """
        Saves chat history. Since history is now transient, this method updates the in-memory history.

        Args:
            entries: The list of HistoryEntry objects to save.
        """
        self._history = self._filter_entries(entries)

    def delete_history(self) -> bool:
        """
        Deletes the in-memory chat history.

        Returns:
            True if history was cleared, False otherwise.
        """
        if self._history:
            self._history = []
            logger.info("In-memory chat history cleared.")
            return True
        return False
