import logging

import discord
from discord import Message, Reaction, User
from discord.ext import commands

from bard.bot.lifecycle.events import DiscordEventHandler
from bard.bot.lifecycle.presence import PresenceManager
from bard.bot.message.parser import MessageParser
from bard.util.system.lifecycle import TaskLifecycleManager
from config import Config

logger = logging.getLogger("Bard")


class BotHandlers(commands.Cog):
    """
    Manages Discord bot events and dispatches them to appropriate handlers.
    This includes handling messages, message edits, deletions, and reactions.
    """

    def __init__(
        self,
        bot: commands.Bot,
        task_lifecycle_manager: TaskLifecycleManager,
        discord_event_handler: DiscordEventHandler,
        config: Config,
        message_parser: MessageParser,
    ):
        """
        Initializes the BotHandlers cog.

        Args:
            bot: The Discord bot instance.
            task_lifecycle_manager: Manages the lifecycle of processing tasks.
            discord_event_handler: Handles Discord-specific events.
            config: Application configuration settings.
            message_parser: Parses Discord messages into structured data.
        """
        self.bot = bot
        self.task_lifecycle_manager = task_lifecycle_manager
        self.discord_event_handler = discord_event_handler
        self.config = config
        self.message_parser = message_parser
        self.presence_manager = PresenceManager(bot, config)

    @commands.Cog.listener()
    async def on_ready(self):
        """
        Handles the bot's 'on_ready' event, which triggers when the bot successfully connects to Discord.
        It logs bot information, sets its presence, and configures the bot's user ID in relevant components.
        """
        if self.bot.user:
            assert self.bot.user is not None
            logger.info(
                f"Bot connected as {self.bot.user.name} (ID: {self.bot.user.id}). Ready."
            )
            logger.debug(f"Discord.py Version: {discord.__version__}")
            logger.debug(
                f"Main Gemini Model: {self.config.MODEL_ID}, Secondary Gemini Model: {self.config.MODEL_ID_SECONDARY}, TTS Gemini Model: {self.config.MODEL_ID_TTS}, Image Generation Gemini Model: {self.config.MODEL_ID_IMAGE_GENERATION}, Voice: {self.config.VOICE_NAME}."
            )
            logger.debug(f"User Memory Max Entries: {self.config.MAX_MEMORIES}.")

            self.discord_event_handler.bot_user_id = self.bot.user.id
            self.message_parser.bot_user_id = self.bot.user.id
            logger.debug(f"Bot user ID set to {self.bot.user.id}.")

            await self.presence_manager.set_presence()
        else:
            logger.warning("Bot user is not available on on_ready event.")

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
        is_mentioned = False
        if message.guild and self.bot.user:
            is_mentioned = self.bot.user.mentioned_in(message)

        if is_dm or is_mentioned:
            await self.task_lifecycle_manager.start_new_task(message)

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

        await self.discord_event_handler.handle_edit(before, after)

    @commands.Cog.listener()
    async def on_message_delete(self, message: Message):
        """
        Handles the 'on_message_delete' event, delegating to DiscordEventHandler.

        Args:
            message: The Discord message object that was deleted.
        """
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

        await self.discord_event_handler.handle_retry_reaction(reaction, user)
        await self.discord_event_handler.handle_cancel_reaction(reaction, user)
