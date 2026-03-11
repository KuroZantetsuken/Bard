import logging

import discord
from async_lru import alru_cache

log = logging.getLogger("Bard")


class MessageCache:
    """
    A simple in-memory cache for Discord messages to reduce API calls.
    Uses async_lru for expiration and size management.
    """

    def __init__(self, maxsize: int = 1000):
        self.maxsize = maxsize

        self._get_message = alru_cache(maxsize=maxsize)(self._fetch_message)

    async def _fetch_message(self, channel: discord.abc.Messageable, message_id: int) -> discord.Message:
        """The actual API call, wrapped by alru_cache."""
        log.debug(f"Fetching message {message_id} from API (cache miss).")
        return await channel.fetch_message(message_id)

    async def get_message(self, channel: discord.abc.Messageable, message_id: int) -> discord.Message:
        """
        Retrieves a message from the cache or fetches it from the API.
        """
        try:
            return await self._get_message(channel, message_id)
        except Exception as e:
            log.error(f"Failed to fetch message {message_id}: {e}")
            raise

    def clear(self):
        """Clears the cache."""
        self._get_message.cache_clear()
