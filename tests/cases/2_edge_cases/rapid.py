import asyncio

from tests.base import BardTestCase


class EdgeCasesRapidTest(BardTestCase):
    async def test_rapid_fire(self):
        """
        Sends 5 messages in 1 second to verify rate limiting handling and that the bot doesn't crash or get stuck.
        """
        for i in range(5):
            await self.bot.send_to_channel(
                f"<@{self.bot.settings.BOT_ID}> Rapid fire {i}"
            )
            await asyncio.sleep(0.1)

        await asyncio.sleep(10)

        check = await self.bot.send_and_wait(
            f"<@{self.bot.settings.BOT_ID}> Are you still alive?"
        )

        self.assertIn("alive", check.content.lower() or "yes", check.content.lower())
        print(f"Response: {check.content}")
