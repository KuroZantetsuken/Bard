import asyncio
import logging
import sys

from bard.util.logging import LoggingConfigurator

if __name__ == "__main__":
    logger = logging.getLogger("Bard")

    LoggingConfigurator().setup()
    try:
        from bard.bot.bot import run

        asyncio.run(run())
    except Exception:
        logger.critical(
            "Main: Critical unhandled error during bot execution.", exc_info=True
        )

        sys.exit(1)
    finally:
        logger.debug("Main: Application exiting.")
