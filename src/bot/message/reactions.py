import logging
from typing import Optional, Tuple

import discord
from discord import Message, Reaction, User

from bot.types import Request

log = logging.getLogger("Bard")


class ReactionManager:
    """
    Manages adding and removing reactions to Discord messages.
    Encapsulates the logic for handling reaction-related operations.
    """

    def __init__(self, retry_emoji: str, cancel_emoji: str):
        """
        Initializes the ReactionManager.

        Args:
            retry_emoji: The emoji used for retrying interactions.
            cancel_emoji: The emoji used to cancel a response generation.
        """
        self.retry_emoji = retry_emoji
        self.cancel_emoji = cancel_emoji
        log.debug("ReactionManager initialized.")

    async def handle_request_creation(self, request: Request):
        """Adds the cancel emoji to the user's message when a request is created."""
        message: Optional[Message] = request.data.get("message")
        if not message:
            return

        try:
            await message.add_reaction(self.cancel_emoji)
            request.data["cancel_emoji_added"] = True
            log.debug(
                "Added cancel reaction to message.", extra={"message_id": message.id}
            )
        except discord.HTTPException as e:
            log.warning(
                "Failed to add cancel reaction.",
                extra={"message_id": message.id, "error": e},
            )

    async def handle_request_completion(
        self, request: Request, tool_emojis: Optional[list[str]] = None
    ):
        """Handles reactions for a completed request."""
        user_message: Optional[Message] = request.data.get("message")
        bot_messages: Optional[list[Message]] = request.data.get("bot_messages")

        if user_message and request.data.get("cancel_emoji_added"):
            try:
                bot_user = None
                if user_message.guild:
                    bot_user = user_message.guild.me
                elif isinstance(
                    user_message.channel, (discord.DMChannel, discord.GroupChannel)
                ):
                    bot_user = user_message.channel.me

                if bot_user:
                    await user_message.remove_reaction(self.cancel_emoji, bot_user)
            except discord.HTTPException as e:
                log.warning(
                    "Failed to remove cancel reaction from user message.",
                    extra={"message_id": user_message.id, "error": e},
                )

        if bot_messages:
            first_bot_message = bot_messages[0]
            await self.add_reactions(first_bot_message, tool_emojis)

    async def handle_request_cancellation(
        self, request: Request, is_edit: bool = False
    ):
        """Handles reactions for a cancelled request."""
        user_message: Optional[Message] = request.data.get("message")
        bot_messages: Optional[list[Message]] = request.data.get("bot_messages")

        if user_message:
            try:
                await user_message.clear_reactions()
            except discord.HTTPException as e:
                log.warning(
                    "Failed to clear reactions from user message.",
                    extra={"message_id": user_message.id, "error": e},
                )
            if not is_edit:
                try:
                    await user_message.add_reaction(self.retry_emoji)
                except discord.HTTPException as e:
                    log.warning(
                        "Failed to add retry reaction to user message.",
                        extra={"message_id": user_message.id, "error": e},
                    )

        if bot_messages:
            for bot_message in bot_messages:
                try:
                    await bot_message.clear_reactions()
                except discord.HTTPException as e:
                    log.warning(
                        "Failed to clear reactions from bot message.",
                        extra={"message_id": bot_message.id, "error": e},
                    )

    async def handle_request_error(self, request: Request):
        """Handles reactions for a request that resulted in an error."""
        bot_messages: Optional[list[Message]] = request.data.get("bot_messages")
        if bot_messages:
            await self.add_reactions(bot_messages[0])

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
            log.debug(
                "Adding retry reaction.",
                extra={"message_id": message.id, "emoji": self.retry_emoji},
            )
            await message.add_reaction(self.retry_emoji)
        except discord.HTTPException as e:
            log.warning(
                "Could not add retry reaction.",
                extra={"message_id": message.id, "error": e},
            )

        if tool_emojis:
            log.debug(
                "Adding tool emojis.",
                extra={"message_id": message.id, "emojis": tool_emojis},
            )
            for emoji in tool_emojis:
                try:
                    await message.add_reaction(emoji)
                except discord.HTTPException as e:
                    log.warning(
                        "Could not add tool emoji reaction.",
                        extra={"message_id": message.id, "emoji": emoji, "error": e},
                    )

    async def remove_reaction(self, reaction_to_remove: Tuple[Reaction, User]) -> None:
        """
        Removes a specific reaction from a message.

        Args:
            reaction_to_remove: A tuple containing the Reaction and User
                                who added the reaction to be removed.
        """
        reaction, user = reaction_to_remove
        try:
            log.debug(
                "Removing reaction.",
                extra={
                    "message_id": reaction.message.id,
                    "user_id": user.id,
                    "emoji": str(reaction.emoji),
                },
            )
            await reaction.remove(user)
        except discord.HTTPException as e:
            log.warning(
                "Failed to remove reaction.",
                extra={"message_id": reaction.message.id, "error": e},
            )
