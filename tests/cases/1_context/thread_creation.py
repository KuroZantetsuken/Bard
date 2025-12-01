import asyncio

import discord

from tests.base import BardTestCase


class ContextThreadingTest(BardTestCase):
    async def test_auto_threading(self):
        """
        Sends a request specifically designed to elicit a response >2000 characters.
        """
        msg = await self.bot.send_and_wait(
            f"<@{self.bot.settings.BOT_ID}> Write a 2500 character story about anything. Make sure it is longer than 2000 characters. Do NOT use tools."
        )
        await asyncio.sleep(1)

        channel = self.bot.get_channel(msg.channel.id)
        refreshed_msg = discord.Message
        if isinstance(channel, (discord.TextChannel, discord.Thread)):
            refreshed_msg = await channel.fetch_message(msg.id)
            
        self.assertTrue(refreshed_msg.thread)
        print(f"Response: {msg.content}")
