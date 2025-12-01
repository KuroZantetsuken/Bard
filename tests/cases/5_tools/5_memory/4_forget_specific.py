from tests.base import BardTestCase


class MemoryDeleteTest(BardTestCase):
    async def test_memory_delete(self):
        """
        Verifies removing a specific fact. Assumes memory populated by 2_add_several.py.
        """
        check1 = await self.bot.send_and_wait(
            f"<@{self.bot.settings.BOT_ID}> What is my pin code?"
        )
        self.assertIn("1234", check1.content)
        print(f"Response: {check1.content}")

        request = await self.bot.send_and_wait(
            f"<@{self.bot.settings.BOT_ID}> Forget my pin code."
        )
        print(f"Response: {request.content}")

        check2 = await self.bot.send_and_wait(
            f"<@{self.bot.settings.BOT_ID}> What is my pin code?"
        )
        self.assertNotIn("1234", check2.content)
        print(f"Response: {check2.content}")
