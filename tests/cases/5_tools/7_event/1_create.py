from tests.base import BardTestCase


class EventCreateTest(BardTestCase):
    async def test_event_create(self):
        """
        Schedule an event.
        """
        response = await self.bot.send_and_wait(
            f"<@{self.bot.settings.BOT_ID}> Schedule an event called 'Game Night' for tomorrow at 10 PM lasting for 2 hours."
        )

        channel = await self.get_test_guild_channel()
        guild = channel.guild

        events = await guild.fetch_scheduled_events()

        self.assertIn("scheduled", response.content.lower())
        self.assertIn("Game Night", response.content)
        self.assertEqual(len(events), 1)
        print(f"Response: {response.content}")
