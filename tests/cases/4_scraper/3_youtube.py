from tests.base import BardTestCase


class ScraperYouTubeTest(BardTestCase):
    async def test_youtube_scraping(self):
        """
        Sends a YouTube URL and verifies the bot uses the specific YouTube understanding along with generic HTML scraping.
        """
        url = "https://www.youtube.com/watch?v=98DcoXwGX6I"
        self.clear_url_cache(url)

        response = await self.bot.send_and_wait(
            f"<@{self.bot.settings.BOT_ID}> What is this video about? {url}"
        )

        content_lower = response.content.lower()
        expected_keywords = ["gemini", "deepmind", "google", "intelligence", "model"]
        found_keywords = [kw for kw in expected_keywords if kw in content_lower]

        self.assertTrue(
            len(found_keywords) >= 2,
            f"Expected keywords from video, found {found_keywords}. Content: {response.content}",
        )
        print(f"Response: {response.content}")
