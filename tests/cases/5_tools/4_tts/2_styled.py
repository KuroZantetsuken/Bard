from tests.base import BardTestCase


class ToolsTTSTest(BardTestCase):
    async def test_tools_tts(self):
        """
        Requests a voice message with a specific emotion.
        Needs manual verification.
        """
        response = await self.bot.send_and_wait(
            f"<@{self.bot.settings.BOT_ID}> Say 'Oh no!' as a desparate voice message."
        )

        self.assertHasAudioAttachment(response)
        print(f"Styled TTS: {len(response.attachments)} attachments")
