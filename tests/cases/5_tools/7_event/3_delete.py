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

        content_lower = response.content.lower()
        self.assertTrue("cancelled" in content_lower or "deleted" in content_lower or "removed" in content_lower)
        
        found = any(e.name == "Game Night" for e in events)
        self.assertFalse(found, f"Event 'Game Night' still found in guild events: {[e.name for e in events]}")
        print(f"Response: {response.content}")
