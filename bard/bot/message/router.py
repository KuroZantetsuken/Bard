import discord

from config import Config


class CommandRouter:
    """
    A lightweight, stateless pre-filter for incoming messages.
    Its primary function is to identify messages that are clearly commands,
    thereby preventing them from undergoing the more extensive AI processing lifecycle.
    """

    @staticmethod
    def is_command(message: discord.Message) -> bool:
        """
        Checks if a given message is a command by verifying if its content
        starts with the configured command prefix.

        Args:
            message: The Discord message object to check.

        Returns:
            True if the message content begins with the command prefix, False otherwise.
        """
        return message.content.startswith(Config.COMMAND_PREFIX)
