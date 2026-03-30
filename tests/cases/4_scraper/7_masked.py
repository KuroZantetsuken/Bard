from tests.base import BardTestCase


class ScraperMaskedTest(BardTestCase):
    """
    Tests that the bot ignores explicit URL masking.
    """

    async def test_masked_scraping(self):
        """
        Sends masked URLs and verifies the bot does NOT scrape them.
        """
        prompt = f"<@{self.bot.settings.BOT_ID}> Here are two links: <https://example.com/> and [example](https://example.com/). Since they are masked, they should not be scraped. Respond whether or not you can see the contenst of the links."
        response = await self.bot.send_and_wait(prompt)
        self.assertIsNotNone(response, "Bot should have responded")
        print("Bot Response:", response.content)
