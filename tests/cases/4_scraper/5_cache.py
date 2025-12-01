import time

from tests.base import BardTestCase


class ScraperCacheTest(BardTestCase):
    async def test_scraper_cache_hit(self):
        """
        Verifies that subsequent requests for the same URL are served from cache
        and are significantly faster.
        """
        url = "https://x.com/northstardoll/status/1993693764389376252"
        self.clear_url_cache(url)

        start_time = time.time()
        pop1 = await self.bot.send_and_wait(
            f"<@{self.bot.settings.BOT_ID}> Summarize this page: {url}"
        )
        duration_cold = time.time() - start_time
        print(f"Cold cache duration: {duration_cold:.2f}s")
        self.assertIsNotNone(pop1.content)
        print(f"Response: {pop1.content}")

        start_time = time.time()
        hit1 = await self.bot.send_and_wait(
            f"<@{self.bot.settings.BOT_ID}> Read this link again: {url}"
        )
        duration_warm = time.time() - start_time
        print(f"Warm cache duration: {duration_warm:.2f}s")
        self.assertIsNotNone(hit1.content)
        print(f"Response: {hit1.content}")

        url_video = "https://www.reddit.com/r/TikTokCringe/comments/1jurhia/moving_a_photo_in_microsoft_word/"
        self.clear_url_cache(url_video)

        print("Testing video cache...")
        start_time = time.time()
        pop2 = await self.bot.send_and_wait(
            f"<@{self.bot.settings.BOT_ID}> Check this video: {url_video}"
        )
        duration_video_cold = time.time() - start_time
        print(f"Video cold duration: {duration_video_cold:.2f}s")
        self.assertIsNotNone(pop2.content)
        print(f"Response: {pop2.content}")

        start_time = time.time()
        hit2 = await self.bot.send_and_wait(
            f"<@{self.bot.settings.BOT_ID}> Check this video again: {url_video}"
        )
        duration_video_warm = time.time() - start_time
        print(f"Video warm duration: {duration_video_warm:.2f}s")
        self.assertIsNotNone(hit2.content)
        print(f"Response: {hit2.content}")

        self.assertTrue(
            duration_warm < duration_cold, duration_video_warm < duration_video_cold
        )
