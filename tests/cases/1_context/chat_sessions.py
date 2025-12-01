from tests.base import BardTestCase


class ContextMultiTurnTest(BardTestCase):
    async def test_chat_sessions(self):
        """
        Simulates multiple, separate requests to verify chat sessions are separated by reply chains.
        """
        msg1 = await self.bot.send_and_wait(
            f"<@{self.bot.settings.BOT_ID}> Hello, my name is TestUser. Do NOT use any tools to remember this."
        )
        print(f"Response: {msg1.content}")

        msg2 = await self.bot.send_and_wait(
            f"<@{self.bot.settings.BOT_ID}> What is my name?"
        )
        self.assertNotIn("TestUser", msg2.content)
        print(f"Response: {msg2.content}")

        msg3 = await self.bot.send_and_wait(
            f"<@{self.bot.settings.BOT_ID}> What is my name?", reference=msg1
        )
        self.assertIn("TestUser", msg3.content)
        print(f"Bot recalled name: {msg3.content}")
