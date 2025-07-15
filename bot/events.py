import logging
from typing import Optional

import discord
from discord import Message, Reaction, User

from config import Config
from utilities.lifecycle import TaskLifecycleManager

# Initialize logger for the Discord event handler module.
logger = logging.getLogger("Bard")


class DiscordEventHandler:
    """
    Handles Discord events that may modify an in-flight message processing task.
    It delegates to the TaskLifecycleManager for orchestration of these tasks,
    such as message edits, deletions, and reaction-based retries.
    """

    def __init__(
        self,
        task_lifecycle_manager: TaskLifecycleManager,
        config: Config,
        bot_user_id: Optional[int] = None,
    ):
        """
        Initializes the DiscordEventHandler.

        Args:
            task_lifecycle_manager: An instance of TaskLifecycleManager to manage tasks.
            config: An instance of Config containing application settings.
            bot_user_id: The Discord user ID of the bot.
        """
        self.task_lifecycle_manager = task_lifecycle_manager
        self.config = config
        self.bot_user_id = bot_user_id

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
        # Ignore embed-only edits.
        if before.content == after.content and not before.embeds and after.embeds:
            return

        # Cancel any existing task for this message.
        if after.id in self.task_lifecycle_manager.active_processing_tasks:
            self.task_lifecycle_manager.cancel_task_for_message(after.id)

        # Determine if the edited message should trigger a new processing task.
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

        # Override to reprocess if the message is part of an active conversation.
        existing_bot_responses = self.task_lifecycle_manager.active_bot_responses.get(
            after.id
        )
        if not should_process_after and existing_bot_responses:
            should_process_after = True

        if not should_process_after:
            # Delete old bot responses if the message is no longer relevant.
            if existing_bot_responses:
                first_message = existing_bot_responses[0]
                if first_message.thread:
                    # If the message started a thread, only delete the starter message.
                    try:
                        await first_message.delete()
                    except discord.HTTPException as e:
                        logger.warning(
                            f"Could not delete previous bot thread starter {first_message.id}: {e}."
                        )
                else:
                    # Otherwise, delete all associated messages.
                    for msg in existing_bot_responses:
                        try:
                            await msg.delete()
                        except discord.HTTPException as e:
                            logger.warning(
                                f"Could not delete previous bot response {msg.id}: {e}."
                            )
                self.task_lifecycle_manager.active_bot_responses.pop(after.id, None)
            return

        # Start a new task for the edited message.
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
        self.task_lifecycle_manager.cancel_task_for_message(message.id)

        # Delete all associated bot responses.
        if message.id in self.task_lifecycle_manager.active_bot_responses:
            bot_responses = self.task_lifecycle_manager.active_bot_responses.pop(
                message.id, []
            )
            if bot_responses:
                first_message = bot_responses[0]
                if first_message.thread:
                    # If the message started a thread, only delete the starter message.
                    try:
                        await first_message.delete()
                    except discord.HTTPException as e:
                        logger.warning(
                            f"Could not delete bot thread starter {first_message.id}: {e}."
                        )
                else:
                    # Otherwise, delete all associated messages.
                    for bot_response in bot_responses:
                        try:
                            await bot_response.delete()
                        except discord.HTTPException as e:
                            logger.warning(
                                f"Could not delete bot response {bot_response.id}: {e}."
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
        # Filter out reactions from bots, or reactions not on bot's messages, or not the retry emoji.
        if (
            user.bot
            or reaction.message.author.id != self.bot_user_id
            or str(reaction.emoji) != self.config.RETRY_EMOJI
        ):
            return

        # Find the original message ID that this bot response is a reply to.
        original_message_id = next(
            (
                msg_id
                for msg_id, bot_msgs in self.task_lifecycle_manager.active_bot_responses.items()
                if reaction.message.id in [m.id for m in bot_msgs]
            ),
            None,
        )
        if not original_message_id:
            return

        # Fetch the original message.
        try:
            original_message = await reaction.message.channel.fetch_message(
                original_message_id
            )
        except discord.HTTPException as e:
            logger.error(
                f"Failed to fetch original message {original_message_id} for retry: {e}."
            )
            return

        # Validate that the user who reacted is the original author of the message.
        if original_message.author.id != user.id:
            return

        # Start a new task to reprocess the original message.
        bot_messages_to_edit = self.task_lifecycle_manager.active_bot_responses.get(
            original_message.id
        )
        await self.task_lifecycle_manager.start_new_task(
            original_message,
            bot_messages_to_edit=bot_messages_to_edit,
            reaction_to_remove=(reaction, user),
        )
