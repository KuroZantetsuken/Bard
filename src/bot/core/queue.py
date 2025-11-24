import asyncio
import logging
from typing import Any, Dict, List, Optional

from bot.message.sender import MessageSender
from bot.types import Request

log = logging.getLogger("Bard")


class MessageQueue:
    """
    Manages a message queue system for outbound Discord messages, ensuring
    messages are sent sequentially and handling rate limits more gracefully.
    """

    def __init__(self, message_sender: MessageSender):
        """
        Initializes the MessageQueue.

        Args:
            message_sender: The service used to send messages to Discord.
        """
        self.message_sender = message_sender
        self.queues: Dict[int, asyncio.Queue] = {}
        self.worker_tasks: List[asyncio.Task] = []
        self._running = False
        log.debug("MessageQueue initialized.")

    def get_queue(self, channel_id: int) -> asyncio.Queue:
        """
        Retrieves or creates a queue for the specified channel ID.

        Args:
            channel_id: The ID of the Discord channel.

        Returns:
            The asyncio.Queue for the channel.
        """
        if channel_id not in self.queues:
            self.queues[channel_id] = asyncio.Queue()
            log.debug(f"Created new message queue for channel {channel_id}.")

            # If workers are already running, start a worker for this new queue
            if self._running:
                task = asyncio.create_task(
                    self._process_queue(channel_id, self.queues[channel_id])
                )
                self.worker_tasks.append(task)

        return self.queues[channel_id]

    async def enqueue(
        self,
        channel_id: int,
        message_data: Dict[str, Any],
        request: Optional[Request] = None,
    ):
        """
        Enqueues a message for sending.

        Args:
            channel_id: The ID of the channel to send to.
            message_data: A dictionary containing the arguments for MessageSender.send.
            request: Optional Request object to update with sent messages.
        """
        queue = self.get_queue(channel_id)
        await queue.put((message_data, request))
        log.debug(
            f"Enqueued message for channel {channel_id}. Queue size: {queue.qsize()}"
        )

    def start_workers(self):
        """
        Starts worker tasks for all existing queues and sets the running flag.
        """
        if self._running:
            log.warning("MessageQueue workers are already running.")
            return

        self._running = True
        log.info("Starting MessageQueue workers.")
        for channel_id, queue in self.queues.items():
            task = asyncio.create_task(self._process_queue(channel_id, queue))
            self.worker_tasks.append(task)

    async def stop_workers(self):
        """
        Stops all worker tasks.
        """
        self._running = False
        log.info("Stopping MessageQueue workers.")
        for task in self.worker_tasks:
            task.cancel()

        if self.worker_tasks:
            await asyncio.gather(*self.worker_tasks, return_exceptions=True)

        self.worker_tasks.clear()

    async def _process_queue(self, channel_id: int, queue: asyncio.Queue):
        """
        Worker task that processes messages from a specific channel's queue.

        Args:
            channel_id: The ID of the channel.
            queue: The queue to process.
        """
        log.debug(f"Started worker for channel {channel_id}.")
        while self._running:
            try:
                item = await queue.get()
                message_data, request = item
                log.debug(f"Processing message for channel {channel_id}.")

                try:
                    sent_messages = await self.message_sender.send(**message_data)
                    if request and sent_messages:
                        # Append to existing messages if any, though usually this is one batch per request per queue item
                        # Use set default to be safe
                        current_messages = request.data.get("bot_messages", [])
                        # Avoid duplicates if logic changes
                        current_ids = {m.id for m in current_messages}
                        new_messages = [
                            m for m in sent_messages if m.id not in current_ids
                        ]
                        if new_messages:
                            request.data["bot_messages"] = (
                                current_messages + new_messages
                            )

                except Exception as e:
                    log.error(
                        f"Error sending message in channel {channel_id}: {e}",
                        exc_info=True,
                    )
                finally:
                    queue.task_done()

            except asyncio.CancelledError:
                log.debug(f"Worker for channel {channel_id} cancelled.")
                break
            except Exception as e:
                log.error(
                    f"Unexpected error in worker for channel {channel_id}: {e}",
                    exc_info=True,
                )
                # Brief pause to prevent tight loops in case of persistent errors
                await asyncio.sleep(1)
