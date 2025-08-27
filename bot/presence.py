import logging

import discord

from config import Config

logger = logging.getLogger("Bard")


class PresenceManager:
    """
    Manages the bot's presence on Discord, including activity and status.
    """

    def __init__(self, bot: discord.Client, config: Config):
        """
        Initializes the PresenceManager.

        Args:
            bot: The Discord bot instance.
            config: The application configuration settings.
        """
        self.bot = bot
        self.config = config

    async def set_presence(self):
        """
        Sets the bot's presence based on the configuration.
        """
        try:
            activity = None
            activity_type = self.config.PRESENCE_TYPE.lower()

            if activity_type == "playing":
                activity = discord.Game(name=self.config.PRESENCE_TEXT)
            elif activity_type == "listening":
                activity = discord.Activity(
                    type=discord.ActivityType.listening,
                    name=self.config.PRESENCE_TEXT,
                )
            elif activity_type == "watching":
                activity = discord.Activity(
                    type=discord.ActivityType.watching,
                    name=self.config.PRESENCE_TEXT,
                )
            elif activity_type == "custom":
                # Convert the emoji string to a PartialEmoji object
                presence_emoji = None
                if self.config.PRESENCE_EMOJI:
                    try:
                        presence_emoji = discord.PartialEmoji.from_str(
                            self.config.PRESENCE_EMOJI
                        )
                    except Exception as e:
                        logger.warning(
                            f"Could not parse PRESENCE_EMOJI '{self.config.PRESENCE_EMOJI}' into PartialEmoji: {e}"
                        )
                        presence_emoji = None  # Ensure it's None if parsing fails

                activity = discord.CustomActivity(
                    name=self.config.PRESENCE_TEXT, emoji=presence_emoji
                )
            if activity:
                await self.bot.change_presence(activity=activity)
                logger.debug(
                    f"Bot presence set to '{activity_type}' with text '{self.config.PRESENCE_TEXT}'."
                )
            else:
                logger.warning(f"Invalid presence type: {self.config.PRESENCE_TYPE}")

        except Exception as e:
            logger.warning(f"Could not set bot presence: {e}.")
