from tests.base import BardTestCase


class MediaRepliesTest(BardTestCase):
    async def test_media_context_in_replies(self):
        """
        Initiates a conversation with an image, then triggers a reply chain asking questions
        about that image to ensure visual context persists across messages.
        """
        msg1 = await self.send_image(
            filename="image.jpg",
            content=f"<@{self.bot.settings.BOT_ID}> What is in this image?",
        )
        print(f"Response: {msg1.content}")

        msg2 = await self.bot.send_and_wait(
            f"<@{self.bot.settings.BOT_ID}> Tell me more about the colors used.",
            reference=msg1,
        )

        content_lower = msg2.content.lower()
        expected_keywords = ["green", "yellow", "red", "color", "zone", "distance"]
        found_keywords = [kw for kw in expected_keywords if kw in content_lower]

        self.assertTrue(
            len(found_keywords) >= 2,
            f"Expected color-related keywords, found {found_keywords}. Content: {msg2.content}",
        )
        print(f"Response: {msg2.content}")
