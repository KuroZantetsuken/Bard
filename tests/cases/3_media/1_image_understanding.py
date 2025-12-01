from tests.base import BardTestCase


class MediaImagesTest(BardTestCase):
    async def test_image_understanding(self):
        """
        Uploads test_image.png and verifies the bot can describe specific visual elements.
        """
        response = await self.send_image(
            filename="image.jpg",
            content=f"<@{self.bot.settings.BOT_ID}> Describe this image in detail.",
        )

        content_lower = response.content.lower()
        expected_keywords = ["camera", "dog", "bulldog", "line"]
        found_keywords = [kw for kw in expected_keywords if kw in content_lower]

        self.assertTrue(
            len(found_keywords) >= 2,
            f"Expected at least 2 keywords from {expected_keywords}, found {found_keywords}. Content: {response.content}",
        )
        print(f"Response: {response.content}")
