from tests.base import BardTestCase


class EventListTest(BardTestCase):
    async def test_event_list(self):
        """
        Retrieve scheduled meetings. Assumes memory populated by 1_create.py.
        """
        response = await self.bot.send_and_wait(
            f"<@{self.bot.settings.BOT_ID}> List the upcoming events."
        )
        self.assertIn("Game Night", response.content)
        print(f"Response: {response.content}")
