from tests.base import BardTestCase


class MemoryAddTest(BardTestCase):
    async def test_memory_add(self):
        """
        Verifies saving a new memory.
        """
        self.clear_memory_cache()
        memory_file = self.get_memory_file()

        response = await self.bot.send_and_wait(
            f"<@{self.bot.settings.BOT_ID}> Use your memory tool to remember that my favorite color is teal."
        )

        self.assertTrue(memory_file.exists())
        print(f"Response: {response.content}")
