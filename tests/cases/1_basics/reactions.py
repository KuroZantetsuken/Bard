import discord
from tests.base import BardTestCase


class BugReproTest(BardTestCase):
    async def test_reaction_added(self):
        """
        Test to verify that the bot adds a cancel reaction when a request is created.
        """
        msg = await self.bot.send_and_wait(
            f"<@{self.bot.settings.BOT_ID}> This is a test message to check for reactions."
        )
        print(f"Response: {msg.content}")
        # Refresh the message to be sure
        channel = self.bot.get_channel(msg.channel.id)
        if isinstance(channel, (discord.TextChannel, discord.Thread, discord.VoiceChannel, discord.StageChannel, discord.DMChannel, discord.GroupChannel)):
            refreshed_msg = await channel.fetch_message(msg.id)
        else:
            self.fail(f"Channel {channel} does not support fetch_message")
        
        print(f"Reactions on bot message: {refreshed_msg.reactions}")
        
        retry_emoji = getattr(self.bot.settings, "RETRY_EMOJI", "🔄")

        has_retry = any(str(r.emoji) == retry_emoji for r in refreshed_msg.reactions)
        self.assertTrue(has_retry, "Bot message should have a retry reaction")
