import asyncio
import logging
import sys

from log import setup_logging


def run_app():
    """Sets up logging and runs the bot."""
    setup_logging()
    log = logging.getLogger("Bard")

    try:
        from bot.bot import run

        log.info("Starting bot...")
        asyncio.run(run())
    except Exception:
        log.critical("Critical unhandled error during bot execution.", exc_info=True)
        sys.exit(1)
    finally:
        log.info("Bot stopped.")


if __name__ == "__main__":
    run_app()
