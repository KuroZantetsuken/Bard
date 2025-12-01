import asyncio
import logging
import os

import discord
from discord.ext import commands

from bot.core.container import Container
from bot.core.handlers import BotHandlers
from settings import Settings

log = logging.getLogger("Bard")


async def run():
    """
    Runs the Discord bot. This function initializes the bot, sets up its intents,
    configures dependency injection, registers event handlers,
    and starts the Discord connection.
    """
    log.debug("Bot run sequence initiated.")
    settings = Settings()
    settings.validate_settings()
    log.debug("Settings loaded and validated.")

    signal_path = os.path.join(settings.CACHE_DIR, "bot_ready")
    if os.path.exists(signal_path):
        try:
            os.remove(signal_path)
            log.debug(f"Removed stale readiness signal file: {signal_path}")
        except Exception as e:
            log.warning(f"Failed to remove stale readiness signal file: {e}")

    if not settings.DISCORD_BOT_TOKEN:
        log.error("DISCORD_BOT_TOKEN is not set. Halting execution.")
        raise ValueError(
            "DISCORD_BOT_TOKEN is not set. Please check your configuration."
        )

    intents = discord.Intents.all()
    intents.message_content = True
    intents.members = True
    log.debug("Discord intents configured.", extra={"data": dict(intents)})

    bot = commands.Bot(
        command_prefix=commands.when_mentioned_or(""),
        intents=intents,
        help_command=None,
    )
    log.debug("Discord bot object created.")

    container = Container(settings)
    scraper = container.get("scraper")
    log.debug("DI container and scraper initialized.")

    try:
        async with scraper:
            log.debug("Registering bot handlers cog.")
            await bot.add_cog(
                BotHandlers(
                    bot,
                    container.get("request_manager"),
                    container.get("discord_event_handler"),
                    settings,
                    container.get("message_parser"),
                )
            )
            log.info("Attempting to connect to Discord and start bot.")
            message_queue = container.get("message_queue")
            message_queue.start_workers()

            await bot.start(settings.DISCORD_BOT_TOKEN)
    except Exception as e:
        log.critical(f"Critical error during bot execution: {e}", exc_info=True)
    finally:
        log.debug("Bot shutdown sequence initiated.")
        if scraper:
            log.debug("Closing scraper.")
            await scraper.close()

        try:
            message_queue = container.get("message_queue")
            await message_queue.stop_workers()
        except Exception:
            pass

        try:
            if os.path.exists(signal_path):
                os.remove(signal_path)
                log.debug(f"Removed readiness signal file: {signal_path}")
        except Exception as e:
            log.warning(f"Failed to remove readiness signal file: {e}")

        log.info("Bot has been shut down.")


if __name__ == "__main__":
    asyncio.run(run())
