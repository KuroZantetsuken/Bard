import logging
from typing import Optional

import discord

logger = logging.getLogger("Bard")


class MessageManager:
    """
    A service for managing Discord messages, including deletion and reaction removal.
    """

    def __init__(self, logger: logging.Logger):
        """
        Initializes the MessageManager service.

        Args:
            logger: The configured logger instance for diagnostics.
        """
        self.logger = logger

    async def delete_message(self, message: discord.Message):
        """
        Deletes a Discord message.

        Args:
            message: The discord.Message object to delete.
        """
        try:
            await message.delete()
        except discord.NotFound:
            self.logger.warning(
                f"Message not found, could not delete. ID: {message.id}."
            )
        except discord.Forbidden:
            self.logger.error(
                f"Bot lacks permissions to delete message ID: {message.id}."
            )
        except discord.HTTPException as e:
            self.logger.error(f"Error deleting message ID: {message.id}: {e}.")
        except Exception as e:
            self.logger.error(
                f"An unexpected error occurred while deleting message (ID: {message.id}): {e}"
            )

    async def remove_reaction(
        self, message: discord.Message, emoji: str, user: Optional[discord.User] = None
    ):
        """
        Removes a reaction from a Discord message.

        Args:
            message: The discord.Message object from which to remove the reaction.
            emoji: The emoji (str) to remove.
            user: Optional; The discord.User whose reaction to remove. If None, removes bot's own reaction.
        """
        try:
            if user:
                await message.remove_reaction(emoji, user)
            else:
                await message.remove_reaction(emoji, message.author)
        except discord.NotFound:
            self.logger.warning(
                f"Message or emoji not found, could not remove reaction '{emoji}' from message ID {message.id}."
            )
        except discord.Forbidden:
            self.logger.error(
                f"Bot lacks permissions to remove reaction '{emoji}' from message ID {message.id}."
            )
        except discord.HTTPException as e:
            self.logger.error(
                f"Error removing reaction '{emoji}' from message ID {message.id}: {e}."
            )
        except Exception as e:
            self.logger.error(
                f"Error removing reaction '{emoji}' from message ID {message.id}: {e}."
            )
