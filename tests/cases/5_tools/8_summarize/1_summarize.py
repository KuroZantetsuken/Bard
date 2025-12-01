from tests.base import BardTestCase


class ToolsSummarizeTest(BardTestCase):
    async def test_tools_summarize(self):
        """
        Requests a summary of specific conversation history populated manually.
        Set up test discussion manually.
        """
        response = await self.bot.send_and_wait(
            f"<@{self.bot.settings.BOT_ID}> Summarize the conversation from 06:15 AM to 06:30 AM CET on November 29, 2025."
        )

        content_lower = response.content.lower()
        keywords = [
            "chimera",
            "december",
            "alice",
            "bob",
            "aws",
            "python",
            "postgresql",
            "react",
        ]

        found = [kw for kw in keywords if kw in content_lower]
        self.assertTrue(
            len(found) >= 4,
            f"Expected summary keywords, found {found}.",
        )
        print(f"Response: {response.content}")
