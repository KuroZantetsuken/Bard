from tests.base import BardTestCase


class MemoryForgetAllTest(BardTestCase):
    async def test_memory_forget_all(self):
        """
        Verifies clearing all user memory. Assumes memory populated by 2_add_several.py.
        """
        check1 = await self.bot.send_and_wait(
            f"<@{self.bot.settings.BOT_ID}> What do you know about me?"
        )
        content_lower = check1.content.lower()
        expected_keywords = ["teal", "iguana", "1234"]
        found_keywords = [kw for kw in expected_keywords if kw in content_lower]

        self.assertTrue(
            len(found_keywords) >= 3,
            f"Expected keywords from memories, found {found_keywords}.",
        )
        print(f"Response: {check1.content}")

        await self.bot.send_and_wait(
            f"<@{self.bot.settings.BOT_ID}> Forget everything you know about me."
        )

        check2 = await self.bot.send_and_wait(
            f"<@{self.bot.settings.BOT_ID}> Use your memory tool to tell me what A is."
        )

        content_lower = check2.content.lower()
        found_keywords = [kw for kw in expected_keywords if kw in content_lower]

        self.assertTrue(
            len(found_keywords) == 0,
            f"Expected keywords from memories, found {found_keywords}.",
        )
        print(f"Response: {check2.content}")
