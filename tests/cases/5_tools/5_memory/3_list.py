from tests.base import BardTestCase


class MemoryReadTest(BardTestCase):
    async def test_memory_read(self):
        """
        Verifies retrieving saved facts. Assumes memory populated by 2_add_several.py.
        """
        check1 = await self.bot.send_and_wait(
            f"<@{self.bot.settings.BOT_ID}> What do you know about me?"
        )
        self.assertIn("1234", check1.content)
        print(f"Response: {check1.content}")

        response = await self.bot.send_and_wait(
            f"<@{self.bot.settings.BOT_ID}> What do you remember about me?"
        )

        content_lower = response.content.lower()
        expected_keywords = ["teal", "iguana", "1234"]
        found_keywords = [kw for kw in expected_keywords if kw in content_lower]

        self.assertTrue(
            len(found_keywords) >= 3,
            f"Expected keywords from memories, found {found_keywords}.",
        )
        print(f"Response: {response.content}")
