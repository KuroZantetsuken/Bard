from tests.base import BardTestCase


class CodeMathTest(BardTestCase):
    async def test_code_math(self):
        """
        Complex calculation.
        """
        response = await self.bot.send_and_wait(
            f"<@{self.bot.settings.BOT_ID}> Calculate the 100th Fibonacci number."
        )
        self.assertEqual(len(response.attachments), 1)
        self.assertIn("354224848179261915075", response.content.replace(",", ""))
        print(f"Code Math: {response.content}")
