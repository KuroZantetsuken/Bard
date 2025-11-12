import asyncio
import logging
from typing import List, Optional, Tuple

import discord
from discord import Message, Reaction, User

from bot.core.coordinator import Coordinator
from bot.core.lifecycle import RequestManager
from bot.core.typing import TypingManager
from bot.message.reactions import ReactionManager
from bot.types import RequestState
from settings import Settings

log = logging.getLogger("Bard")


class DiscordEventHandler:
    """
    Handles Discord events that may modify an in-flight message processing task.
    It delegates to the RequestManager for orchestration of these tasks,
    such as message edits, deletions, and reaction-based retries.
    """

    def __init__(
        self,
        request_manager: RequestManager,
        coordinator: Coordinator,
        reaction_manager: ReactionManager,
        typing_manager: TypingManager,
        settings: Settings,
        bot_user_id: Optional[int] = None,
    ):
        """
        Initializes the DiscordEventHandler.

        Args:
            request_manager: An instance of RequestManager to manage requests.
            coordinator: An instance of the Coordinator to process requests.
            reaction_manager: An instance of the ReactionManager to manage reactions.
            typing_manager: An instance of the TypingManager to manage typing indicators.
            settings: An instance of Settings containing application settings.
            bot_user_id: The Discord user ID of the bot.
        """
        self.request_manager = request_manager
        self.coordinator = coordinator
        self.reaction_manager = reaction_manager
        self.typing_manager = typing_manager
        self.settings = settings
        self.bot_user_id = bot_user_id
        log.debug("DiscordEventHandler initialized.")

    async def _start_new_request(
        self,
        message: Message,
        bot_messages_to_edit: Optional[List[Message]] = None,
        reaction_to_remove: Optional[Tuple[Reaction, User]] = None,
    ):
        request = self.request_manager.create_request(
            data={"message": message, "original_message_id": message.id}
        )
        await self.reaction_manager.handle_request_creation(request)
        task = asyncio.create_task(
            self.coordinator.process(request, bot_messages_to_edit, reaction_to_remove)
        )
        self.request_manager.assign_task_to_request(request.id, task)
        log.debug(f"Started processing request {request.id} for message {message.id}")

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

        for request in self.request_manager._requests.values():
            if request.data.get("original_message_id") == after.id:
                await self.request_manager.cancel_request(request.id, is_edit=True)

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

        existing_bot_responses = None
        for request in self.request_manager._requests.values():
            if request.data.get("original_message_id") == after.id:
                if "bot_messages" in request.data:
                    existing_bot_responses = request.data["bot_messages"]
                    break

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
            return

        log.info(
            "Starting new request for edited message.",
            extra={"message_id": after.id},
        )
        await self._start_new_request(
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
            "Cancelling request due to message deletion.",
            extra={"message_id": message.id},
        )

        requests_to_cancel = []
        bot_responses_to_delete = []
        for request in self.request_manager._requests.values():
            if request.data.get("original_message_id") == message.id:
                requests_to_cancel.append(request.id)
                if "bot_messages" in request.data:
                    bot_responses_to_delete.extend(request.data["bot_messages"])

        for request_id in requests_to_cancel:
            await self.request_manager.cancel_request(request_id)

        if bot_responses_to_delete:
            log.info(
                "Deleting bot responses associated with deleted message.",
                extra={"message_id": message.id},
            )
            first_message = bot_responses_to_delete[0]
            if first_message.thread:
                try:
                    await first_message.delete()
                except discord.HTTPException as e:
                    log.warning(
                        "Could not delete bot thread starter.",
                        extra={"message_id": first_message.id, "error": e},
                    )
            else:
                for bot_response in bot_responses_to_delete:
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

        if user.bot or str(reaction.emoji) != self.settings.RETRY_EMOJI:
            return

        log.debug(
            "Handling retry reaction.",
            extra={
                "message_id": reaction.message.id,
                "user_id": user.id,
                "emoji": str(reaction.emoji),
            },
        )

        request_to_retry = None

        if reaction.message.author.id == self.bot_user_id:
            for request in self.request_manager._requests.values():
                if "bot_messages" in request.data and any(
                    m.id == reaction.message.id for m in request.data["bot_messages"]
                ):
                    request_to_retry = request
                    break

        else:
            for request in self.request_manager._requests.values():
                if request.data.get("original_message_id") == reaction.message.id:
                    request_to_retry = request
                    break

        if not request_to_retry:
            log.debug(
                "No active request found for this reaction.",
                extra={"message_id": reaction.message.id},
            )
            return

        try:
            original_message = await reaction.message.channel.fetch_message(
                request_to_retry.data["original_message_id"]
            )
        except discord.HTTPException as e:
            log.error(
                "Failed to fetch original message for retry.",
                extra={
                    "message_id": request_to_retry.data["original_message_id"],
                    "error": e,
                },
            )
            return

        if original_message.author.id != user.id:
            log.warning(
                "Retry reaction user does not match original message author.",
                extra={"message_id": original_message.id, "reactor_id": user.id},
            )
            return

        bot_messages_to_edit = request_to_retry.data.get("bot_messages")
        if bot_messages_to_edit:
            for msg in bot_messages_to_edit:
                try:
                    await msg.clear_reactions()
                except discord.HTTPException:
                    pass
        else:
            try:
                await original_message.clear_reactions()
            except discord.HTTPException:
                pass

        log.info(
            "Starting new request for retry.", extra={"message_id": original_message.id}
        )

        await self._start_new_request(
            original_message,
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

        if str(reaction.emoji) == self.settings.CANCEL_EMOJI:
            request_to_cancel = None
            for request in self.request_manager._requests.values():
                if request.data.get(
                    "original_message_id"
                ) == reaction.message.id and request.state in (
                    RequestState.PENDING,
                    RequestState.PROCESSING,
                ):
                    request_to_cancel = request
                    break

            if request_to_cancel and reaction.message.author.id == user.id:
                log.info(
                    "Cancel reaction detected, cancelling request.",
                    extra={
                        "request_id": request_to_cancel.id,
                        "message_id": reaction.message.id,
                        "user_id": user.id,
                    },
                )
                await self.request_manager.cancel_request(request_to_cancel.id)
