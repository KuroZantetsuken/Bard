from tests.base import BardTestCase


class ImageGenEditTest(BardTestCase):
    async def test_image_gen_edit(self):
        """
        Reply to an existing image with an edit request ("Make it night time").
        """
        msg1 = await self.send_image(
            "image.jpg", f"<@{self.bot.settings.BOT_ID}> Look at this."
        )

        response = await self.bot.send_and_wait(
            f"<@{self.bot.settings.BOT_ID}> Edit this image to make it look like a sketch.",
            reference=msg1,
        )
        self.assertEqual(len(response.attachments), 1)
        print(f"Image Gen Edit: {len(response.attachments)} attachments")
