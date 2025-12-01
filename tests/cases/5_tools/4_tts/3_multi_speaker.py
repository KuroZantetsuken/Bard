from tests.base import BardTestCase


class ToolsTTSTest(BardTestCase):
    async def test_tools_tts(self):
        """
        Requests a voice message with multiple speakers.
        Needs manual verification.
        """
        response = await self.bot.send_and_wait(
            f"<@{self.bot.settings.BOT_ID}> Say 'Hello World' as a voice message."
        )

        self.assertHasAudioAttachment(response)
        print(f"Multi-speaker TTS: {len(response.attachments)} attachments")
