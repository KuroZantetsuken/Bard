from tests.base import BardTestCase


class ContextDeepChainTest(BardTestCase):
    async def test_reply_chains(self):
        """
        Simulates a conversation 5+ levels deep to verify context retention.
        """
        msg = await self.bot.send_and_wait(
            f"<@{self.bot.settings.BOT_ID}> Let's play a memory game. I will give you words, and you repeat them all at the end. Do NOT use tools."
        )
        print(f"Response: {msg.content}")

        words = ["Apple", "Banana", "Cherry", "Date", "Elderberry"]
        for word in words:
            msg = await self.bot.send_and_wait(
                f"<@{self.bot.settings.BOT_ID}> The next word is {word}.",
                reference=msg,
            )
            print(f"Response: {msg.content}")

        final_msg = await self.bot.send_and_wait(
            f"<@{self.bot.settings.BOT_ID}> List all {len(words)} words I gave you.",
            reference=msg,
        )

        content = final_msg.content.lower()

        for word in words:
            self.assertIn(word.lower(), content)
        print(f"Response: {final_msg.content}")
