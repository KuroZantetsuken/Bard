from tests.base import BardTestCase


class CodeMathTest(BardTestCase):
    async def test_code_math(self):
        """
        Graph plotting.
        """
        response = await self.bot.send_and_wait(
            f"<@{self.bot.settings.BOT_ID}> Plot a sine wave."
        )
        self.assertEqual(len(response.attachments), 2)
        print(f"Graph plotting: {len(response.attachments)} attachments")
