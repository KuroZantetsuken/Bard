from tests.base import BardTestCase


class DiagnoseListFilesTest(BardTestCase):
    async def test_diagnose_list_files(self):
        """
        List directory contents.
        """
        response = await self.bot.send_and_wait(
            f"<@{self.bot.settings.BOT_ID}> List files in the current directory."
        )
        self.assertIn("requirements.txt", response.content)
        print(f"Response: {response.content}")
