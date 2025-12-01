import asyncio
import logging
import os
from contextlib import AbstractAsyncContextManager
from typing import Dict, Protocol

from settings import Settings


class TypeableChannel(Protocol):
    id: int

    def typing(self) -> AbstractAsyncContextManager[None]: ...


log = logging.getLogger("Bard")


class TypingManager:
    """
    Manages the typing indicator for different channels.
    """

    def __init__(self, settings: Settings):
        self._typing_tasks: Dict[int, asyncio.Task] = {}
        self.settings = settings

    def _get_signal_path(self, channel_id: int) -> str:
        return os.path.join(self.settings.CACHE_DIR, f"bot_typing_{channel_id}")

    async def _typing_loop(self, channel: TypeableChannel):
        signal_path = self._get_signal_path(channel.id)
        try:
            os.makedirs(os.path.dirname(signal_path), exist_ok=True)
            with open(signal_path, "w") as f:
                f.write("typing")

            async with channel.typing():
                await asyncio.Future()
        except asyncio.CancelledError:
            pass
        except Exception as e:
            log.warning(
                f"Error in typing loop for channel {channel.id}: {e}", exc_info=True
            )
        finally:
            try:
                if os.path.exists(signal_path):
                    os.remove(signal_path)
            except Exception as e:
                log.warning(f"Failed to remove typing signal for {channel.id}: {e}")

    def start_typing(self, channel: TypeableChannel):
        """
        Starts the typing indicator in the specified channel.
        """
        if channel.id in self._typing_tasks:
            return

        task = asyncio.create_task(self._typing_loop(channel))
        self._typing_tasks[channel.id] = task
        log.debug(f"Started typing in channel {channel.id}.")

    def stop_typing(self, channel: TypeableChannel):
        """
        Stops the typing indicator in the specified channel.
        """
        if channel.id not in self._typing_tasks:
            return

        task = self._typing_tasks.pop(channel.id)
        task.cancel()
        log.debug(f"Stopped typing in channel {channel.id}.")
