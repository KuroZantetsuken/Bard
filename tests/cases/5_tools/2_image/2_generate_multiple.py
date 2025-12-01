from tests.base import BardTestCase


class ImageGenCreateTest(BardTestCase):
    async def test_image_gen_create(self):
        """
        Generate multiple images.
        """
        response = await self.bot.send_and_wait(
            f"<@{self.bot.settings.BOT_ID}> Generate an image of a dog, then generate an image of a cat. I want two separate images."
        )
        self.assertEqual(len(response.attachments), 2)
        print(f"Image Gen Create: {len(response.attachments)} attachments")
