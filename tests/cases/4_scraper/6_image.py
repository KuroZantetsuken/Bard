import logging

from tests.base import BardTestCase

log = logging.getLogger("TestRunner")


class ImageScraperTest(BardTestCase):
    async def test_image_search_scraping(self):
        """
        Tests the ImageScraper directly through a tool or by mocking a search.
        """

        channel = await self.get_test_guild_channel()
        guild = channel.guild
        events = await guild.fetch_scheduled_events()
        for event in events:
            try:
                await event.delete()
            except Exception:
                pass
        prompt = (
            f"<@{self.bot.settings.BOT_ID}> Create a Discord event: "
            "'Cyberpunk Party' starting tomorrow at 8pm, ending at 11pm. "
            "Use 'cyberpunk city neon' for the banner image."
        )
        response = await self.bot.send_and_wait(prompt)

        content_lower = response.content.lower()
        self.assertTrue(
            "scheduled" in content_lower or "created" in content_lower,
            f"Response should indicate event was created/scheduled. Got: {response.content}",
        )
        new_events = await guild.fetch_scheduled_events()
        self.assertEqual(len(new_events), 1)
        event = new_events[0]

        log.info(f"Event attributes: {dir(event)}")

        image_attr = getattr(event, "cover_image", None)
        self.assertTrue(
            image_attr is not None,
            f"Event should have a banner image. Attributes found: {dir(event)}",
        )
        print(f"Response: {response.content}")
        print(f"Event Image: {image_attr}")
