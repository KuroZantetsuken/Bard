import asyncio
import datetime
import os

import discord

from src.settings import Settings as AppSettings
from tests.settings import TestSettings


class DummyClient(discord.Client):
    """
    A simple Discord client for black-box testing.
    Listens for messages from the target bot and sends commands.
    """

    def __init__(self, *args, **kwargs):
        intents = discord.Intents.default()
        intents.message_content = True
        intents.members = True
        intents.presences = True
        super().__init__(intents=intents, *args, **kwargs)

        self.settings = TestSettings
        self.target_id = int(self.settings.BOT_ID) if self.settings.BOT_ID else 0
        self.channel_id = (
            int(self.settings.TEST_CHANNEL_ID) if self.settings.TEST_CHANNEL_ID else 0
        )
        self.response_queue = asyncio.Queue()
        self.typing_event = asyncio.Event()

    async def setup_hook(self):
        """
        Called when the client is logged in and ready.
        """
        pass

    async def on_ready(self):
        """
        Called when the client is ready.
        """
        if self.user:
            print(f"DummyClient logged in as {self.user} (ID: {self.user.id})")
        print(f"Targeting bot ID: {self.target_id}")
        print(f"Operating in channel ID: {self.channel_id}")

    async def on_message(self, message: discord.Message):
        """
        Listens for messages.
        """
        if self.user and message.author.id == self.user.id:
            return

        is_target_channel = message.channel.id == self.channel_id
        is_thread_in_channel = (
            isinstance(message.channel, discord.Thread)
            and message.channel.parent_id == self.channel_id
        )

        if not (is_target_channel or is_thread_in_channel):
            return

        if message.author.id == self.target_id:
            await self.response_queue.put(message)

    async def on_typing(
        self,
        channel: discord.abc.Messageable,
        user: discord.User | discord.Member,
        when: datetime.datetime,
    ):
        """
        Called when someone starts typing.
        """
        if user.id != self.target_id:
            return

        chan_id = getattr(channel, "id", None)

        if not chan_id:
            return

        if chan_id != self.channel_id:
            if (
                isinstance(channel, discord.Thread)
                and channel.parent_id != self.channel_id
            ):
                return
            elif not isinstance(channel, discord.Thread):
                return

        print(f"Target bot started typing in {channel}")
        self.typing_event.set()

    async def on_member_update(self, before: discord.Member, after: discord.Member):
        """
        Called when a member updates their profile (including status).
        """
        if after.id == self.target_id:
            if after.status != discord.Status.offline:
                print(f"Target bot updated status to: {after.status}")
            else:
                print(f"Target bot updated status to: {after.status} (offline)")

    def clear_queue(self) -> int:
        """
        Clears the response queue. Returns the number of items cleared.
        """
        count = 0
        while not self.response_queue.empty():
            try:
                self.response_queue.get_nowait()
                count += 1
            except asyncio.QueueEmpty:
                break

        self.typing_event.clear()

        return count

    async def send_to_channel(self, content: str):
        """
        Sends a message to the test channel without waiting for a response.
        """
        channel = self.get_channel(self.channel_id)
        if not channel:
            try:
                channel = await self.fetch_channel(self.channel_id)
            except Exception as e:
                raise RuntimeError(
                    f"Could not access test channel {self.channel_id}: {e}"
                )

        if not isinstance(
            channel,
            (
                discord.TextChannel,
                discord.Thread,
                discord.VoiceChannel,
                discord.StageChannel,
            ),
        ):
            raise RuntimeError(
                f"Channel {self.channel_id} is not a messagable channel type: {type(channel)}"
            )

        try:
            await channel.send(content)
        except Exception as e:
            print(f"Failed to send report message: {e}")

    async def send_and_wait(
        self,
        content: str,
        files: list[discord.File] | None = None,
        timeout: int | None = None,
        reference: discord.Message | discord.MessageReference | None = None,
    ) -> discord.Message:
        """
        Sends a message and waits for a response from the target bot.
        """
        if timeout is None:
            timeout = self.settings.RESPONSE_TIMEOUT

        self.clear_queue()

        channel = self.get_channel(self.channel_id)
        if not channel:
            try:
                channel = await self.fetch_channel(self.channel_id)
            except Exception as e:
                raise RuntimeError(
                    f"Could not access test channel {self.channel_id}: {e}"
                )

        if not isinstance(
            channel,
            (
                discord.TextChannel,
                discord.Thread,
                discord.VoiceChannel,
                discord.StageChannel,
            ),
        ):
            raise RuntimeError(
                f"Channel {self.channel_id} is not a messagable channel type: {type(channel)}"
            )

        print(f"Sending: {content} (Files: {len(files) if files else 0})")

        kwargs = {}
        if reference:
            kwargs["reference"] = reference

        if files:
            await channel.send(content, files=files, **kwargs)
        else:
            await channel.send(content, **kwargs)

        started = await self.wait_for_typing(timeout=5)
        if not started:
            raise TimeoutError("Target bot did not start processing within 5 seconds.")

        try:
            print(f"Waiting for response (timeout={timeout}s)...")
            response = await asyncio.wait_for(
                self.response_queue.get(), timeout=timeout
            )
            print(f"Received response: {response.content[:50]}...")
            return response
        except asyncio.TimeoutError:
            raise TimeoutError(
                f"Timed out waiting for response from bot {self.target_id} after {timeout} seconds."
            )

    async def wait_for_response(self, timeout: int | None = None) -> discord.Message:
        """
        Waits for a response from the target bot.
        """
        if timeout is None:
            timeout = self.settings.RESPONSE_TIMEOUT

        try:
            print(f"Waiting for response (timeout={timeout}s)...")
            response = await asyncio.wait_for(
                self.response_queue.get(), timeout=timeout
            )
            print(f"Received response: {response.content[:50]}...")
            return response
        except asyncio.TimeoutError:
            raise TimeoutError(
                f"Timed out waiting for response from bot {self.target_id} after {timeout} seconds."
            )

    async def wait_for_typing(self, timeout: int = 5) -> bool:
        """
        Waits for the target bot to signal processing via file system.
        """
        print(f"Waiting for processing signal (timeout={timeout}s)...")
        signal_path = os.path.join(
            AppSettings.CACHE_DIR, f"bot_typing_{self.channel_id}"
        )

        end_time = asyncio.get_running_loop().time() + timeout
        while asyncio.get_running_loop().time() < end_time:
            if os.path.exists(signal_path):
                print("Processing signal detected!")
                return True
            await asyncio.sleep(0.1)

        print(f"Timed out waiting for processing signal after {timeout}s")
        return False

    async def wait_for_target_presence(self, timeout: int = 30) -> bool:
        """
        Waits for the target bot to signal readiness via file system.
        """
        print(f"Waiting for bot readiness signal (timeout={timeout}s)...")
        signal_path = os.path.join(AppSettings.CACHE_DIR, "bot_ready")

        end_time = asyncio.get_running_loop().time() + timeout
        while asyncio.get_running_loop().time() < end_time:
            if os.path.exists(signal_path):
                try:
                    with open(signal_path, "r") as f:
                        content = f.read().strip()
                    if content == str(self.target_id):
                        print("Bot readiness signal detected!")
                        return True
                except Exception:
                    pass
            await asyncio.sleep(0.5)

        print("Timed out waiting for bot readiness signal.")
        return False
