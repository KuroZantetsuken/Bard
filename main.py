import asyncio
import logging
import sys

from utilities.logging import setup_logging_config

if __name__ == "__main__":
    # Initialize the main logger for the application.
    logger = logging.getLogger("Bard")
    # Configure the global logging settings.
    setup_logging_config()
    try:
        # Import the main bot run function.
        from bot.bot import run

        # Run the bot asynchronously.
        asyncio.run(run())
    except Exception:
        # Log any critical unhandled errors during bot execution.
        logger.critical(
            "Main: Critical unhandled error during bot execution.", exc_info=True
        )
        # Exit the application with an error code.
        sys.exit(1)
    finally:
        # Log application exit.
        logger.debug("Main: Application exiting.")
