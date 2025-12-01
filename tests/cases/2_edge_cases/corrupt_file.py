import io

import discord

from tests.base import BardTestCase


class EdgeCasesEmptyTest(BardTestCase):
    async def test_invalid_attachment(self):
        """
        Messages with only invalid attachments to verify graceful failure.
        """
        fake_file = discord.File(io.BytesIO(b"Not an image"), filename="fake_image.png")

        try:
            response = await self.bot.send_and_wait(
                f"<@{self.bot.settings.BOT_ID}>", files=[fake_file]
            )
            print(f"Invalid Attachment Response: {response.content}")
        except Exception as e:
            print(f"Invalid Attachment Exception: {e}")
