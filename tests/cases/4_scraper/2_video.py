from tests.base import BardTestCase


class ScraperVideoTest(BardTestCase):
    async def test_video_url_scraping(self):
        """
        Sends a URL to a non-YouTube video platform to verify generic video URL handling.
        """
        url = "https://www.reddit.com/r/TikTokCringe/comments/1jurhia/moving_a_photo_in_microsoft_word/"
        self.clear_url_cache(url)

        response = await self.bot.send_and_wait(
            f"<@{self.bot.settings.BOT_ID}> Summarize this video link: {url}"
        )

        content_lower = response.content.lower()
        expected_keywords = ["moving", "photo", "word", "microsoft", "frustrations"]
        found_keywords = [kw for kw in expected_keywords if kw in content_lower]

        self.assertTrue(
            len(found_keywords) >= 2,
            f"Expected keywords from reddit video, found {found_keywords}. Content: {response.content}",
        )
        print(f"Response: {response.content}")
