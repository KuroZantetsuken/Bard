import logging
from typing import Optional

import discord

log = logging.getLogger("Bard")


class MessageManager:
    """
    A service for managing Discord messages, including deletion and reaction removal.
    """

    def __init__(self):
        """
        Initializes the MessageManager service.
        """
        log.debug("MessageManager initialized.")

    async def delete_message(self, message: discord.Message):
        """
        Deletes a Discord message.

        Args:
            message: The discord.Message object to delete.
        """
        try:
            log.debug("Attempting to delete message.", extra={"message_id": message.id})
            await message.delete()
            log.info("Message deleted successfully.", extra={"message_id": message.id})
        except discord.NotFound:
            log.warning(
                "Message not found, could not delete.", extra={"message_id": message.id}
            )
        except discord.Forbidden:
            log.error(
                "Bot lacks permissions to delete message.",
                extra={"message_id": message.id},
            )
        except discord.HTTPException as e:
            log.error(
                "HTTP error while deleting message.",
                extra={"message_id": message.id, "error": e},
            )
        except Exception as e:
            log.error(
                "An unexpected error occurred while deleting message.",
                extra={"message_id": message.id, "error": e},
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
            target_user = user or message.author
            log.debug(
                "Attempting to remove reaction.",
                extra={
                    "message_id": message.id,
                    "emoji": emoji,
                    "user_id": target_user.id,
                },
            )
            await message.remove_reaction(emoji, target_user)
            log.info(
                "Reaction removed successfully.",
                extra={
                    "message_id": message.id,
                    "emoji": emoji,
                    "user_id": target_user.id,
                },
            )
        except discord.NotFound:
            log.warning(
                "Message or emoji not found, could not remove reaction.",
                extra={"message_id": message.id, "emoji": emoji},
            )
        except discord.Forbidden:
            log.error(
                "Bot lacks permissions to remove reaction.",
                extra={"message_id": message.id, "emoji": emoji},
            )
        except discord.HTTPException as e:
            log.error(
                "HTTP error while removing reaction.",
                extra={"message_id": message.id, "emoji": emoji, "error": e},
            )
        except Exception as e:
            log.error(
                "An unexpected error occurred while removing reaction.",
                extra={"message_id": message.id, "emoji": emoji, "error": e},
            )
