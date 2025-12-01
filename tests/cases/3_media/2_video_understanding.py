from tests.base import BardTestCase


class MediaVideosTest(BardTestCase):
    async def test_video_understanding(self):
        """
        Uploads test_video.mp4 and requests a summary to verify video processing and frame analysis.
        """
        response = await self.send_video(
            filename="video.mp4",
            content=f"<@{self.bot.settings.BOT_ID}> Summarize this video.",
        )

        content_lower = response.content.lower()
        expected_keywords = [
            "hair",
            "hoodie",
            "clothes",
            "packing",
            "socks",
            "underwear",
        ]
        found_keywords = [kw for kw in expected_keywords if kw in content_lower]

        self.assertTrue(
            len(found_keywords) >= 2,
            f"Expected video keywords, found {found_keywords}. Content: {response.content}",
        )
        print(f"Response: {response.content}")
