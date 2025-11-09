import logging
from typing import Optional

import discord
from discord import Message, Reaction, User

from bot.lifecycle.tasks import TaskLifecycleManager
from settings import Settings

log = logging.getLogger("Bard")


class DiscordEventHandler:
    """
    Handles Discord events that may modify an in-flight message processing task.
    It delegates to the TaskLifecycleManager for orchestration of these tasks,
    such as message edits, deletions, and reaction-based retries.
    """

    def __init__(
        self,
        task_lifecycle_manager: TaskLifecycleManager,
        settings: Settings,
        bot_user_id: Optional[int] = None,
    ):
        """
        Initializes the DiscordEventHandler.

        Args:
            task_lifecycle_manager: An instance of TaskLifecycleManager to manage tasks.
            settings: An instance of Settings containing application settings.
            bot_user_id: The Discord user ID of the bot.
        """
        self.task_lifecycle_manager = task_lifecycle_manager
        self.settings = settings
        self.bot_user_id = bot_user_id
        log.debug("DiscordEventHandler initialized.")

    async def handle_edit(self, before: Message, after: Message):
        """
        Handles message edit events.
        If the message content has changed and the message is relevant to the bot,
        any existing processing task for that message is cancelled, and a new one is started.
        If the message is no longer relevant, associated bot responses are deleted.

        Args:
            before: The message object before the edit.
            after: The message object after the edit.
        """

        if before.content == after.content and not before.embeds and after.embeds:
            log.debug(
                "Ignoring message edit due to embed-only change.",
                extra={"message_id": after.id},
            )
            return

        if after.id in self.task_lifecycle_manager.active_processing_tasks:
            log.info(
                "Cancelling existing task due to message edit.",
                extra={"message_id": after.id},
            )
            self.task_lifecycle_manager.cancel_task_for_message(after.id)

        is_dm = isinstance(after.channel, discord.DMChannel)
        is_mentioned = False
        if after.guild:
            if self.bot_user_id is not None:
                member = after.guild.get_member(self.bot_user_id)
            else:
                member = None
            if member:
                is_mentioned = member.mentioned_in(after)
        should_process_after = is_dm or is_mentioned

        existing_bot_responses = self.task_lifecycle_manager.active_bot_responses.get(
            after.id
        )
        if not should_process_after and existing_bot_responses:
            should_process_after = True

        if not should_process_after:
            if existing_bot_responses:
                log.info(
                    "Message is no longer relevant, deleting previous bot responses.",
                    extra={"message_id": after.id},
                )
                first_message = existing_bot_responses[0]
                if first_message.thread:
                    try:
                        await first_message.delete()
                    except discord.HTTPException as e:
                        log.warning(
                            "Could not delete previous bot thread starter.",
                            extra={"message_id": first_message.id, "error": e},
                        )
                else:
                    for msg in existing_bot_responses:
                        try:
                            await msg.delete()
                        except discord.HTTPException as e:
                            log.warning(
                                "Could not delete previous bot response.",
                                extra={"message_id": msg.id, "error": e},
                            )
                self.task_lifecycle_manager.active_bot_responses.pop(after.id, None)
            return

        log.info(
            "Starting new task for edited message.",
            extra={"message_id": after.id},
        )
        await self.task_lifecycle_manager.start_new_task(
            after, bot_messages_to_edit=existing_bot_responses
        )

    async def handle_delete(self, message: Message):
        """
        Handles message deletion events.
        Any processing task associated with the deleted message is cancelled,
        and all bot responses linked to that message are deleted.

        Args:
            message: The Discord message object that was deleted.
        """
        log.info(
            "Cancelling task due to message deletion.",
            extra={"message_id": message.id},
        )
        self.task_lifecycle_manager.cancel_task_for_message(message.id)

        if message.id in self.task_lifecycle_manager.active_bot_responses:
            log.info(
                "Deleting bot responses associated with deleted message.",
                extra={"message_id": message.id},
            )
            bot_responses = self.task_lifecycle_manager.active_bot_responses.pop(
                message.id, []
            )
            if bot_responses:
                first_message = bot_responses[0]
                if first_message.thread:
                    try:
                        await first_message.delete()
                    except discord.HTTPException as e:
                        log.warning(
                            "Could not delete bot thread starter.",
                            extra={"message_id": first_message.id, "error": e},
                        )
                else:
                    for bot_response in bot_responses:
                        try:
                            await bot_response.delete()
                        except discord.HTTPException as e:
                            log.warning(
                                "Could not delete bot response.",
                                extra={"message_id": bot_response.id, "error": e},
                            )

    async def handle_retry_reaction(self, reaction: Reaction, user: User):
        """
        Handles reaction add events, specifically for the retry functionality.
        If the reaction is the configured retry emoji on a bot's message, and the reactor
        is the original author of the message that the bot replied to,
        the original message is reprocessed.

        Args:
            reaction: The Discord Reaction object.
            user: The Discord User who added the reaction.
        """

        if (
            user.bot
            or reaction.message.author.id != self.bot_user_id
            or str(reaction.emoji) != self.settings.RETRY_EMOJI
        ):
            return

        log.debug(
            "Handling retry reaction.",
            extra={
                "message_id": reaction.message.id,
                "user_id": user.id,
                "emoji": str(reaction.emoji),
            },
        )

        message_id = next(
            (
                msg_id
                for msg_id, bot_msgs in self.task_lifecycle_manager.active_bot_responses.items()
                if reaction.message.id in [m.id for m in bot_msgs]
            ),
            None,
        )
        if not message_id:
            log.debug(
                "No active bot response found for this reaction.",
                extra={"message_id": reaction.message.id},
            )
            return

        try:
            message = await reaction.message.channel.fetch_message(message_id)
        except discord.HTTPException as e:
            log.error(
                "Failed to fetch original message for retry.",
                extra={"message_id": message_id, "error": e},
            )
            return

        if message.author.id != user.id:
            log.warning(
                "Retry reaction user does not match original message author.",
                extra={"message_id": message.id, "reactor_id": user.id},
            )
            return

        log.info("Starting new task for retry.", extra={"message_id": message.id})
        bot_messages_to_edit = self.task_lifecycle_manager.active_bot_responses.get(
            message.id
        )
        await self.task_lifecycle_manager.start_new_task(
            message,
            bot_messages_to_edit=bot_messages_to_edit,
            reaction_to_remove=(reaction, user),
        )

    async def handle_cancel_reaction(self, reaction: Reaction, user: User):
        """
        Handles reaction add events for the cancel functionality.
        If the reaction is the configured cancel emoji on a user's message
        that the bot is currently processing, and the reactor is the author
        of that message, the processing task is cancelled.

        Args:
            reaction: The Discord Reaction object.
            user: The Discord User who added the reaction.
        """

        if user.bot:
            return

        if (
            str(reaction.emoji) == self.settings.CANCEL_EMOJI
            and reaction.message.id
            in self.task_lifecycle_manager.active_processing_tasks
        ):
            if reaction.message.author.id == user.id:
                log.info(
                    "Cancel reaction detected, cancelling task.",
                    extra={
                        "message_id": reaction.message.id,
                        "user_id": user.id,
                    },
                )
                self.task_lifecycle_manager.cancel_task_for_message(reaction.message.id)
