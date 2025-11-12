import asyncio
import logging
from contextlib import AbstractAsyncContextManager
from typing import Dict, Protocol


class TypeableChannel(Protocol):
    id: int

    def typing(self) -> AbstractAsyncContextManager[None]: ...


log = logging.getLogger("Bard")


class TypingManager:
    """
    Manages the typing indicator for different channels.
    """

    def __init__(self):
        self._typing_tasks: Dict[int, asyncio.Task] = {}

    async def _typing_loop(self, channel: TypeableChannel):
        try:
            async with channel.typing():
                await asyncio.Future()
        except asyncio.CancelledError:
            pass
        except Exception as e:
            log.warning(
                f"Error in typing loop for channel {channel.id}: {e}", exc_info=True
            )

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
