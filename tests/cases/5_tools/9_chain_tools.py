from tests.base import BardTestCase


class ToolsChainingTest(BardTestCase):
    async def test_tools_chaining(self):
        """
        Complex request requiring chained tool calls.
        """
        self.clear_memory_cache()

        channel = await self.get_test_guild_channel()
        guild = channel.guild

        events = await guild.fetch_scheduled_events()
        for event in events:
            await event.delete()

        response = await self.bot.send_and_wait(
            f"<@{self.bot.settings.BOT_ID}> List all files in the project, read `instructions.txt`, then follow those instructions."
        )

        self.assertEqual(
            len(response.attachments),
            3,
        )

        content = response.content.lower()

        self.assertTrue(
            "remember" in content
            or "memory" in content
            or "support" in content
            or "note" in content
        )

        events = await guild.fetch_scheduled_events()

        self.assertIn("scheduled", response.content.lower())
        self.assertEqual(len(events), 1)

        self.clear_memory_cache()
        for event in events:
            await event.delete()

        print(f"Response: {response.content}")
