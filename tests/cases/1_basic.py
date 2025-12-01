from tests.base import BardTestCase


class BasicTest(BardTestCase):
    """
    Basic sanity check test case.
    """

    async def test_ping(self):
        """
        Verifies that the main bot responds to a simple ping.
        """
        response = await self.bot.send_and_wait(
            f"<@{self.bot.settings.BOT_ID}> hello there"
        )

        self.assertTrue(response.content)
        print(f"Response: {response.content}")
