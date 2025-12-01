from tests.base import BardTestCase


class DiagnoseReadFileTest(BardTestCase):
    async def test_diagnose_read_file(self):
        """
        Read a safe file.
        """
        response = await self.bot.send_and_wait(
            f"<@{self.bot.settings.BOT_ID}> Read the `README.md` file."
        )
        self.assertIn("# Bard", response.content)
        print(f"Response: {response.content}")
