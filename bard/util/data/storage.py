import asyncio
import json
import logging
import os
from collections import defaultdict
from typing import Any, Dict, List, Optional

logger = logging.getLogger("Bard")


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
            file_suffix: The suffix to append to filenames (e.g., ".history.json").
        """
        self.storage_dir = storage_dir
        self.file_suffix = file_suffix
        self.storage_locks = defaultdict(asyncio.Lock)
        try:
            os.makedirs(self.storage_dir, exist_ok=True)
        except OSError as e:
            logger.error(
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
            logger.error(
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
        async with self.storage_locks[filepath]:
            if not os.path.exists(filepath):
                return []
            try:
                with open(filepath, "r", encoding="utf-8") as f:
                    data = json.load(f)
                if not isinstance(data, list):
                    logger.error(
                        f"Invalid data format (not a list) for {filepath}. Deleting file."
                    )
                    try:
                        os.remove(filepath)
                    except OSError as remove_error:
                        logger.error(
                            f"Error deleting corrupt data file: {remove_error}"
                        )
                    return []
                return data
            except (json.JSONDecodeError, IOError) as e:
                logger.error(f"Error loading data from {filepath}: {e}", exc_info=True)
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
        temp_path = f"{filepath}.tmp"
        async with self.storage_locks[filepath]:
            try:
                with open(temp_path, "w", encoding="utf-8") as f:
                    json.dump(data, f, indent=2)
                os.replace(temp_path, filepath)
            except Exception as e:
                logger.error(f"Error saving data to {filepath}: {e}", exc_info=True)
                if os.path.exists(temp_path):
                    try:
                        os.remove(temp_path)
                    except OSError as remove_error:
                        logger.error(f"Error removing temporary file: {remove_error}")

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
        async with self.storage_locks[filepath]:
            if os.path.exists(filepath):
                try:
                    os.remove(filepath)
                    logger.info(f"Successfully deleted data file: {filepath}")
                    return True
                except OSError as e:
                    logger.error(
                        f"Error deleting data file {filepath}: {e}", exc_info=True
                    )
            return False
