import logging
from typing import Optional, Tuple

import discord
from discord import Message, Reaction, User

logger = logging.getLogger("Bard")


class ReactionManager:
    """
    Manages adding and removing reactions to Discord messages.
    Encapsulates the logic for handling reaction-related operations.
    """

    def __init__(self, retry_emoji: str):
        """
        Initializes the ReactionManager.

        Args:
            retry_emoji: The emoji used for retrying interactions.
        """
        self.retry_emoji = retry_emoji

    async def add_reactions(
        self,
        message: Message,
        tool_emojis: Optional[list[str]] = None,
    ) -> None:
        """
        Adds reactions to a given Discord message.

        Args:
            message: The Discord message object to add reactions to.
            tool_emojis: Optional list of tool emojis to add as reactions.
        """
        try:
            await message.add_reaction(self.retry_emoji)
        except discord.HTTPException as e:
            logger.warning(f"Could not add retry reaction to message {message.id}: {e}")

        if tool_emojis:
            for emoji in tool_emojis:
                try:
                    await message.add_reaction(emoji)
                except discord.HTTPException as e:
                    logger.warning(
                        f"Could not add tool emoji reaction '{emoji}' to message {message.id}: {e}"
                    )

    async def remove_reaction(self, reaction_to_remove: Tuple[Reaction, User]) -> None:
        """
        Removes a specific reaction from a message.

        Args:
            reaction_to_remove: A tuple containing the Reaction object and the User
                                who added the reaction to be removed.
        """
        reaction, user = reaction_to_remove
        try:
            await reaction.remove(user)
        except discord.HTTPException as e:
            logger.warning(
                f"Failed to remove reaction for message ID {reaction.message.id}: {e}"
            )
