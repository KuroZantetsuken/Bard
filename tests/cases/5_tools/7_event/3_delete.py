from tests.base import BardTestCase


class EventDeleteTest(BardTestCase):
    async def test_event_delete(self):
        """
        Cancel an event. Assumes memory populated by 1_create.py.
        """
        response = await self.bot.send_and_wait(
            f"<@{self.bot.settings.BOT_ID}> Cancel the event called 'Game Night'."
        )

        channel = await self.get_test_guild_channel()
        guild = channel.guild

        events = await guild.fetch_scheduled_events()

        self.assertIn("cancelled", response.content.lower())
        self.assertEqual(len(events), 0)
        print(f"Response: {response.content}")
