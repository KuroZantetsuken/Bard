import asyncio
import logging

import discord
from discord.ext import commands

from bard.bot.core.container import Container
from bard.bot.core.handlers import BotHandlers
from config import Config

logger = logging.getLogger("Bard")


async def run():
    """
    Runs the Discord bot. This function initializes the bot, sets up its intents,
    configures dependency injection, registers event handlers,
    and starts the Discord connection.
    """
    Config.load_and_validate()
    config = Config()

    if not config.DISCORD_BOT_TOKEN:
        raise ValueError(
            "DISCORD_BOT_TOKEN is not set. Please check your configuration."
        )

    intents = discord.Intents.all()
    intents.message_content = True
    intents.members = True

    bot = commands.Bot(command_prefix=commands.when_mentioned_or(""), intents=intents)

    try:
        container = Container(config)

        await bot.add_cog(
            BotHandlers(
                bot,
                container.get("task_lifecycle_manager"),
                container.get("discord_event_handler"),
                config,
                container.get("message_parser"),
            )
        )
        logger.info("Attempting to connect to Discord and start bot.")

        await bot.start(config.DISCORD_BOT_TOKEN)
    except Exception as e:
        logger.critical(f"Critical error during bot execution: {e}", exc_info=True)


if __name__ == "__main__":
    asyncio.run(run())
