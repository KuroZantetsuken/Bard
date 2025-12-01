from tests.base import BardTestCase


class ImageGenCreateTest(BardTestCase):
    async def test_image_gen_create(self):
        """
        Generate an image.
        """
        response = await self.bot.send_and_wait(
            f"<@{self.bot.settings.BOT_ID}> Generate an image of a cyberpunk city."
        )
        self.assertEqual(len(response.attachments), 1)
        print(f"Image Gen Create: {len(response.attachments)} attachments")
