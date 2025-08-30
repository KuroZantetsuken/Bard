import logging
from typing import List, Optional, Tuple

import discord

from ai.context import ChatHistoryManager

from .sender import MessageSender

# Initialize logger for the command handler module.
logger = logging.getLogger("Bard")


class CommandHandler:
    """
    Handles Discord bot commands such as !reset.
    """

    def __init__(
        self,
        context_manager: ChatHistoryManager,
        message_sender: MessageSender,
    ):
        """
        Initializes the CommandHandler with necessary dependencies.

        Args:
            context_manager: An instance of ChatHistoryManager for managing chat history.
            message_sender: An instance of MessageSender for sending messages to Discord.
        """
        self.context_manager = context_manager

        self.message_sender = message_sender

    async def _send_command_error_response(
        self,
        message: discord.Message,
        error_text: str,
        bot_messages_to_edit: Optional[List[discord.Message]] = None,
    ):
        """
        Sends an error response to the Discord channel for invalid command usage.

        Args:
            message: The original Discord message that triggered the command.
            error_text: The error message to send.
            bot_messages_to_edit: Optional list of bot messages to edit instead of sending a new one.
        """
        await self.message_sender.send(
            message, error_text, existing_bot_messages_to_edit=bot_messages_to_edit
        )

    async def handle_reset_command(
        self,
        message: discord.Message,
        guild_id: Optional[int],
        user_id: int,
        bot_messages_to_edit: Optional[List[discord.Message]] = None,
    ) -> bool:
        """
        Handles the `!reset` command, which clears the user's chat history.

        Args:
            message: The Discord message object for the command.
            guild_id: The ID of the Discord guild where the command was issued (None for DMs).
            user_id: The ID of the user who issued the command.
            bot_messages_to_edit: Optional list of bot messages to edit.

        Returns:
            True if the command was successfully handled, False otherwise.
        """
        try:
            deleted = await self.context_manager.delete_history(guild_id, str(user_id))
            response = (
                "🧹 Chat history has been cleared!"
                if deleted
                else "No active chat history found to clear."
            )
        except Exception as e:
            logger.error(
                f"Error resetting chat history for user {user_id}: {e}",
                exc_info=True,
            )
            response = "❌ An error occurred while resetting chat history."
        await self.message_sender.send(
            message, response, existing_bot_messages_to_edit=bot_messages_to_edit
        )
        return True

    async def process_command(
        self,
        message: discord.Message,
        guild_id: Optional[int],
        user_id: int,
        bot_messages_to_edit: Optional[List[discord.Message]] = None,
    ) -> Tuple[bool, bool]:
        """
        Processes supported commands from a Discord message.

        Args:
            message: The Discord message object containing the command.
            guild_id: The ID of the Discord guild where the command was issued (None for DMs).
            user_id: The ID of the user who issued the command.
            bot_messages_to_edit: Optional list of bot messages to edit.

        Returns:
            A tuple where:
            - The first boolean indicates if a command was handled.
            - The second boolean indicates if the handled command was `!reset`.
        """
        content = message.content.strip().lower()
        command_parts = content.split()
        if not command_parts:
            return False, False
        command = command_parts[0]

        if command == "!reset":
            if len(command_parts) > 1:
                await self._send_command_error_response(
                    message,
                    "⚠️ The `!reset` command does not take any arguments.",
                    bot_messages_to_edit,
                )
                return True, False
            was_handled = await self.handle_reset_command(
                message, guild_id, user_id, bot_messages_to_edit
            )
            return was_handled, was_handled

        return False, False
