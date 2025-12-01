from tests.base import BardTestCase


class ScraperStandardTest(BardTestCase):
    async def test_standard_scraping(self):
        """
        Sends a stable text-based URL and verifies the bot can extract and summarize the main content.
        """
        url = "https://x.com/northstardoll/status/1993693764389376252"
        self.clear_url_cache(url)

        response = await self.bot.send_and_wait(
            f"<@{self.bot.settings.BOT_ID}> Summarize this page: {url}"
        )

        content_lower = response.content.lower()
        expected_keywords = ["dog", "puppy", "headphones", "purple"]
        found_keywords = [kw for kw in expected_keywords if kw in content_lower]

        self.assertTrue(
            len(found_keywords) >= 2,
            f"Expected keywords from tweet, found {found_keywords}.",
        )
        print(f"Response: {response.content}")
