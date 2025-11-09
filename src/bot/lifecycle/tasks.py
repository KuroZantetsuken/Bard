import asyncio
import logging
from typing import TYPE_CHECKING, Dict, List, Optional, Tuple

import discord
from discord import Message, Reaction, User

if TYPE_CHECKING:
    from bot.core.coordinator import Coordinator


log = logging.getLogger("Bard")


class TaskLifecycleManager:
    """
    Manages the complete asyncio.Task lifecycle for message processing runs.
    It handles starting, canceling, and tracking the status of asynchronous tasks,
    as well as managing associated bot responses.
    """

    def __init__(self, coordinator: Optional["Coordinator"] = None):
        """
        Initializes the TaskLifecycleManager.

        Args:
            coordinator: An optional instance of the Coordinator. It is set later
                         to resolve circular dependencies.
        """
        self._coordinator = coordinator
        self.active_processing_tasks: Dict[int, asyncio.Task] = {}
        self.active_bot_responses: Dict[int, List[Message]] = {}
        self.active_cancel_reactions: Dict[int, Message] = {}

    def __post_init__(self):
        log.debug("TaskLifecycleManager initialized.")

    @property
    def coordinator(self) -> "Coordinator":
        """
        Provides access to the Coordinator instance. Raises an error if not set.
        """
        if self._coordinator is None:
            log.error("Coordinator accessed before it was set.")
            raise ValueError("Coordinator not set for TaskLifecycleManager.")
        return self._coordinator

    @coordinator.setter
    def coordinator(self, value: "Coordinator"):
        """
        Sets the Coordinator instance.
        """
        self._coordinator = value
        log.debug("Coordinator has been set for TaskLifecycleManager.")

    async def start_new_task(
        self,
        message: Message,
        bot_messages_to_edit: Optional[List[Message]] = None,
        reaction_to_remove: Optional[Tuple[Reaction, User]] = None,
    ):
        """
        Starts a new processing task for a given message.
        If a task for this message already exists, it is cancelled and removed
        before starting the new one to ensure only one task per message is active.

        Args:
            message: The Discord message object to process.
            bot_messages_to_edit: Optional list of bot messages to edit.
            reaction_to_remove: Optional tuple containing a Reaction and User to remove.
        """
        message_id = message.id

        if message_id in self.active_processing_tasks:
            log.warning(
                "A task for this message is already running. Cancelling the old one.",
                extra={"message_id": message_id},
            )
            old_task = self.active_processing_tasks.pop(message_id)
            old_task.cancel()
            await asyncio.gather(old_task, return_exceptions=True)
            log.debug(
                "Old task cancelled and awaited.", extra={"message_id": message_id}
            )

        try:
            await message.add_reaction(self.coordinator.message_sender.cancel_emoji)
            self.active_cancel_reactions[message_id] = message
            log.debug(
                "Added cancel reaction to message.", extra={"message_id": message_id}
            )
        except Exception as e:
            log.warning(
                "Failed to add cancel reaction.",
                extra={"message_id": message_id, "error": e},
            )

        log.info("Creating new processing task.", extra={"message_id": message_id})
        task = asyncio.create_task(
            self.coordinator.process(message, bot_messages_to_edit, reaction_to_remove)
        )
        self.active_processing_tasks[message_id] = task

        task.add_done_callback(lambda t: self._task_done_callback(t, message_id))
        log.debug(
            "Task created and done callback added.", extra={"message_id": message_id}
        )

    def cancel_task_for_message(self, message_id: int):
        """
        Finds and cancels a processing task associated with a specific message ID.

        Args:
            message_id: The ID of the message whose task should be cancelled.
        """
        if message_id in self.active_processing_tasks:
            log.info("Cancelling task for message.", extra={"message_id": message_id})
            task = self.active_processing_tasks.pop(message_id)
            task.cancel()
        else:
            log.debug(
                "No active task found to cancel for message.",
                extra={"message_id": message_id},
            )

    async def _remove_cancel_reaction(self, message_id: int):
        if message_id in self.active_cancel_reactions:
            message = self.active_cancel_reactions.pop(message_id)
            bot_user = None
            if message.guild:
                bot_user = message.guild.me
            elif isinstance(message.channel, (discord.DMChannel, discord.GroupChannel)):
                bot_user = message.channel.me

            if bot_user:
                try:
                    await message.remove_reaction(
                        self.coordinator.message_sender.cancel_emoji, bot_user
                    )
                except Exception as e:
                    log.warning(
                        "Failed to remove cancel reaction.",
                        extra={"message_id": message_id, "error": e},
                    )
            else:
                log.warning(
                    "Could not find bot user to remove cancel reaction.",
                    extra={"message_id": message_id},
                )

    def _task_done_callback(self, task: asyncio.Task, message_id: int):
        """
        Callback function executed when a processing task completes or is cancelled.
        It cleans up the task from the registry and logs any unhandled exceptions.

        Args:
            task: The completed or cancelled asyncio.Task.
            message_id: The ID of the message associated with the task.
        """

        log.debug(
            "Task done callback initiated.",
            extra={"message_id": message_id, "task_status": task.done()},
        )
        self.active_processing_tasks.pop(message_id, None)

        asyncio.create_task(self._remove_cancel_reaction(message_id))

        try:
            if not task.cancelled() and (exc := task.exception()):
                log.critical(
                    "Unhandled exception in processing task.",
                    extra={"message_id": message_id},
                    exc_info=exc,
                )
        except asyncio.CancelledError:
            log.debug(
                "Processing task was cancelled gracefully.",
                extra={"message_id": message_id},
            )
        log.debug(
            "Task done callback finished.",
            extra={"message_id": message_id},
        )
