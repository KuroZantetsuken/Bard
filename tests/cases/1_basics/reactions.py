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
        retry_emoji = getattr(self.bot.settings, "RETRY_EMOJI", "🔄")
        has_retry = await self.wait_for_reaction(msg, retry_emoji)
        self.assertTrue(has_retry, "Bot message should have a retry reaction")
