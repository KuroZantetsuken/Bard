import discord

from tests.base import BardTestCase


class EdgeCasesLimitsAttachmentsTest(BardTestCase):
    async def test_max_attachments(self):
        """
        Tests messages with max attachments (10).
        """
        path = self.get_resource_path("image.jpg")

        files = []
        file_handles = []
        try:
            for i in range(10):
                f = open(path, "rb")
                file_handles.append(f)
                files.append(discord.File(f, filename=f"image_{i}.jpg"))

            response = await self.bot.send_and_wait(
                f"<@{self.bot.settings.BOT_ID}> Count these images.", files=files
            )
            self.assertTrue(
                "10" in response.content or "ten" in response.content.lower()
            )
            print(f"Response: {response.content}")
        finally:
            for f in file_handles:
                f.close()
