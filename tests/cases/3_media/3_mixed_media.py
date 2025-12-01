import discord

from tests.base import BardTestCase


class MediaMixedTest(BardTestCase):
    async def test_mixed_media_understanding(self):
        """
        Uploads both an image and a video in a single message to verify the bot can
        process multiple distinct media attachments simultaneously.
        """
        image_path = self.get_resource_path("image.jpg")
        video_path = self.get_resource_path("video.mp4")

        files = []
        with open(image_path, "rb") as f_img, open(video_path, "rb") as f_vid:
            files.append(discord.File(f_img, filename="image.jpg"))
            files.append(discord.File(f_vid, filename="video.mp4"))

            response = await self.bot.send_and_wait(
                f"<@{self.bot.settings.BOT_ID}> What are these two files?",
                files=files,
            )

        content_lower = response.content.lower()
        expected_keywords = [
            "camera",
            "dog",
            "bulldog",
            "line",
            "hair",
            "hoodie",
            "clothes",
            "packing",
        ]
        found_keywords = [kw for kw in expected_keywords if kw in content_lower]

        self.assertTrue(
            len(found_keywords) >= 3,
            f"Expected keywords from image/video, found {found_keywords}. Content: {response.content}",
        )
        print(f"Response: {response.content}")
