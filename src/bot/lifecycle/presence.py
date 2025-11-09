import logging

import discord

from settings import Settings

log = logging.getLogger("Bard")


class PresenceManager:
    """
    Manages the bot's presence on Discord, including activity and status.
    """

    def __init__(self, bot: discord.Client, settings: Settings):
        """
        Initializes the PresenceManager.

        Args:
            bot: The Discord bot instance.
            settings: The application configuration settings.
        """
        self.bot = bot
        self.settings = settings
        log.debug("PresenceManager initialized.")

    async def set_presence(self):
        """
        Sets the bot's presence based on the configuration.
        """
        try:
            log.debug("Setting bot presence.")
            activity = None
            activity_type = self.settings.PRESENCE_TYPE.lower()
            log.debug(f"Presence type from settings: {activity_type}")

            if activity_type == "playing":
                activity = discord.Game(name=self.settings.PRESENCE_TEXT)
            elif activity_type == "listening":
                activity = discord.Activity(
                    type=discord.ActivityType.listening,
                    name=self.settings.PRESENCE_TEXT,
                )
            elif activity_type == "watching":
                activity = discord.Activity(
                    type=discord.ActivityType.watching,
                    name=self.settings.PRESENCE_TEXT,
                )
            elif activity_type == "custom":
                presence_emoji = None
                if self.settings.PRESENCE_EMOJI:
                    try:
                        presence_emoji = discord.PartialEmoji.from_str(
                            self.settings.PRESENCE_EMOJI
                        )
                    except Exception as e:
                        log.warning(
                            "Could not parse PRESENCE_EMOJI into PartialEmoji.",
                            extra={
                                "presence_emoji": self.settings.PRESENCE_EMOJI,
                                "error": e,
                            },
                        )
                        presence_emoji = None

                activity = discord.CustomActivity(
                    name=self.settings.PRESENCE_TEXT, emoji=presence_emoji
                )

            if activity:
                await self.bot.change_presence(activity=activity)
                log.info(
                    "Bot presence updated successfully.",
                    extra={
                        "type": activity_type,
                        "text": self.settings.PRESENCE_TEXT,
                    },
                )
            else:
                log.warning(
                    "Invalid presence type configured.",
                    extra={"presence_type": self.settings.PRESENCE_TYPE},
                )

        except Exception as e:
            log.warning("Could not set bot presence.", extra={"error": e})
