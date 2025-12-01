from tests.base import BardTestCase


class ScraperRepliesTest(BardTestCase):
    async def test_scraping_context_replies(self):
        """
        Sends a URL, waits for a summary, then replies to that summary asking for specific details
        from the page to verify scraped content remains in context.
        """
        url = "https://x.com/northstardoll/status/1993693764389376252"
        self.clear_url_cache(url)

        msg1 = await self.bot.send_and_wait(
            f"<@{self.bot.settings.BOT_ID}> Look at this: {url}"
        )
        print(f"Response: {msg1.content}")

        msg2 = await self.bot.send_and_wait(
            f"<@{self.bot.settings.BOT_ID}> What is the animal wearing?",
            reference=msg1,
        )

        content_lower = msg2.content.lower()
        expected_keywords = ["headphones", "purple", "wearing"]
        found_keywords = [kw for kw in expected_keywords if kw in content_lower]

        self.assertTrue(
            len(found_keywords) >= 1,
            f"Expected keywords about headphones/purple, found {found_keywords}.",
        )
        print(f"Response: {msg2.content}")
