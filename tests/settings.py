import os

from dotenv import find_dotenv, load_dotenv

load_dotenv(find_dotenv())


class TestSettings:
    """
    Configuration for the testing suite.
    """

    # The user token for the dummy account to use for testing
    TEST_BOT_TOKEN = os.getenv("TEST_BOT_TOKEN")

    # The User ID of the main bot to test against
    BOT_ID = os.getenv("BOT_ID")

    # The Guild ID where tests will run
    TEST_GUILD_ID = os.getenv("TEST_GUILD_ID")

    # The Channel ID where tests will run
    TEST_CHANNEL_ID = os.getenv("TEST_CHANNEL_ID")

    # Timeout for waiting for a response (seconds)
    RESPONSE_TIMEOUT = 300

    @classmethod
    def validate(cls):
        """
        Validates that all required settings are present.
        """
        missing = []
        if not cls.TEST_BOT_TOKEN:
            missing.append("TEST_BOT_TOKEN")
        if not cls.BOT_ID:
            missing.append("BOT_ID")
        if not cls.TEST_GUILD_ID:
            missing.append("TEST_GUILD_ID")
        if not cls.TEST_CHANNEL_ID:
            missing.append("TEST_CHANNEL_ID")

        if missing:
            raise ValueError(f"Missing required test settings: {', '.join(missing)}")

        try:
            if cls.BOT_ID:
                cls.BOT_ID = int(cls.BOT_ID)
            if cls.TEST_GUILD_ID:
                cls.TEST_GUILD_ID = int(cls.TEST_GUILD_ID)
            if cls.TEST_CHANNEL_ID:
                cls.TEST_CHANNEL_ID = int(cls.TEST_CHANNEL_ID)
        except ValueError:
            raise ValueError(
                "BOT_ID, TEST_GUILD_ID, and TEST_CHANNEL_ID must be integers"
            )
