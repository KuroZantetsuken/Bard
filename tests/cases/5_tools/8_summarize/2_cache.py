import time

from tests.base import BardTestCase


class ToolsSummarizeCacheTest(BardTestCase):
    async def test_summary_cache_hit(self):
        """
        Verifies that subsequent summary requests for the same timeframe are served from cache.
        """
        self.clear_summary_cache(
            int(self.bot.settings.TEST_CHANNEL_ID or 0),
            "2025-11-29 05:15",
            "2025-11-29 05:30",
        )

        query = f"<@{self.bot.settings.BOT_ID}> Summarize conversation from 2025-11-29 05:15 to 2025-11-29 05:30 UTC."

        start_time = time.time()
        msg1 = await self.bot.send_and_wait(query)
        duration_cold = time.time() - start_time
        print(f"Cold duration: {duration_cold:.2f}s")
        self.assertIsNotNone(msg1.content)
        print(f"Response: {msg1.content}")

        start_time = time.time()
        msg2 = await self.bot.send_and_wait(query)
        duration_warm = time.time() - start_time
        print(f"Warm duration: {duration_warm:.2f}s")
        self.assertIsNotNone(msg2.content)
        self.assertIn("Project Chimera", msg2.content)
        print(f"Response: {msg2.content}")

        self.assertTrue(duration_warm < duration_cold)
