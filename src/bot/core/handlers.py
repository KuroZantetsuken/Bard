import logging

import discord
from discord import Message, Reaction, User
from discord.ext import commands

from bot.core.events import DiscordEventHandler
from bot.core.lifecycle import RequestManager
from bot.core.presence import PresenceManager
from bot.message.parser import MessageParser
from settings import Settings

log = logging.getLogger("Bard")


class BotHandlers(commands.Cog):
    """
    Manages Discord bot events and dispatches them to appropriate handlers.
    This includes handling messages, message edits, deletions, and reactions.
    """

    def __init__(
        self,
        bot: commands.Bot,
        request_manager: RequestManager,
        discord_event_handler: DiscordEventHandler,
        settings: Settings,
        message_parser: MessageParser,
    ):
        """
        Initializes the BotHandlers cog.

        Args:
            bot: The Discord bot instance.
            request_manager: Manages the lifecycle of processing requests.
            discord_event_handler: Handles Discord-specific events.
            settings: Application configuration settings.
            message_parser: Parses Discord messages into structured data.
        """
        self.bot = bot
        self.request_manager = request_manager
        self.discord_event_handler = discord_event_handler
        self.settings = settings
        self.message_parser = message_parser
        self.presence_manager = PresenceManager(bot, settings)
        log.debug("BotHandlers cog initialized.")

    @commands.Cog.listener()
    async def on_ready(self):
        """
        Handles the bot's 'on_ready' event, which triggers when the bot successfully connects to Discord.
        It logs bot information, sets its presence, and configures the bot's user ID in relevant components.
        """
        if self.bot.user:
            assert self.bot.user is not None
            log.info(
                "Bot connected and ready.",
                extra={
                    "bot_name": self.bot.user.name,
                    "bot_id": self.bot.user.id,
                    "discord_py_version": discord.__version__,
                },
            )
            log.debug(
                "Loaded settings",
                extra={
                    "model_id": self.settings.MODEL_ID,
                    "model_id_secondary": self.settings.MODEL_ID_SECONDARY,
                    "model_id_tts": self.settings.MODEL_ID_TTS,
                    "model_id_image_generation": self.settings.MODEL_ID_IMAGE_GENERATION,
                    "voice_name": self.settings.VOICE_NAME,
                    "max_memories": self.settings.MAX_MEMORIES,
                },
            )

            self.discord_event_handler.bot_user_id = self.bot.user.id
            self.message_parser.bot_user_id = self.bot.user.id
            log.debug(f"Bot user ID set to {self.bot.user.id}.")

            await self.presence_manager.set_presence()
        else:
            log.warning("Bot user is not available on on_ready event.")

    @commands.Cog.listener()
    async def on_message(self, message: Message):
        """
        Handles the 'on_message' event, processing incoming messages.
        It filters out messages from bots, checks for commands, and initiates new tasks
        for direct messages or mentions.

        Args:
            message: The Discord message object.
        """
        assert self.bot.user is not None, "Bot user not initialized."
        if message.author == self.bot.user or message.author.bot:
            return

        is_dm = isinstance(message.channel, discord.DMChannel)
        is_mentioned = self.bot.user.mentioned_in(message) if message.guild else False

        if is_dm or is_mentioned:
            log.info(
                "New message received, starting processing task.",
                extra={
                    "message_id": message.id,
                    "is_dm": is_dm,
                    "is_mentioned": is_mentioned,
                },
            )
            await self.discord_event_handler._start_new_request(message)

    @commands.Cog.listener()
    async def on_message_edit(self, before: Message, after: Message):
        """
        Handles the 'on_message_edit' event, delegating to DiscordEventHandler.

        Args:
            before: The message object before the edit.
            after: The message object after the edit.
        """
        assert self.bot.user is not None, "Bot user not initialized."
        if after.author == self.bot.user or after.author.bot:
            return
        log.debug(
            "Message edit detected.",
            extra={"message_id": after.id, "user_id": after.author.id},
        )
        await self.discord_event_handler.handle_edit(before, after)

    @commands.Cog.listener()
    async def on_message_delete(self, message: Message):
        """
        Handles the 'on_message_delete' event, delegating to DiscordEventHandler.

        Args:
            message: The Discord message object that was deleted.
        """
        log.debug(
            "Message delete detected.",
            extra={"message_id": message.id, "user_id": message.author.id},
        )
        await self.discord_event_handler.handle_delete(message)

    @commands.Cog.listener()
    async def on_reaction_add(self, reaction: Reaction, user: User):
        """
        Handles the 'on_reaction_add' event, delegating to DiscordEventHandler.

        Args:
            reaction: The Discord Reaction object.
            user: The Discord User who added the reaction.
        """
        assert self.bot.user is not None, "Bot user not initialized."
        if user.id == self.bot.user.id:
            return

        log.debug(
            "Reaction added.",
            extra={
                "message_id": reaction.message.id,
                "emoji": str(reaction.emoji),
                "user_id": user.id,
            },
        )
        await self.discord_event_handler.handle_retry_reaction(reaction, user)
        await self.discord_event_handler.handle_cancel_reaction(reaction, user)
