from tests.base import BardTestCase


class SearchSimpleTest(BardTestCase):
    async def test_search_simple(self):
        """
        General knowledge search.
        """
        response = await self.bot.send_and_wait(
            f"<@{self.bot.settings.BOT_ID}> Who won the 2025 Las Vegas Grand Prix?"
        )
        self.assertIn("Max Verstappen", response.content)
        print(f"Search Simple: {response.content}")
