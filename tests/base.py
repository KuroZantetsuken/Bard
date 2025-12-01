import asyncio
import hashlib
import os
import unittest
from pathlib import Path
from urllib.parse import urlparse

import discord

from src.settings import Settings as AppSettings
from tests.dummy import DummyClient


class BardTestCase(unittest.TestCase):
    """
    Base class for all Bard test cases.
    """

    def __init__(self, methodName: str = "runTest", client: DummyClient | None = None):
        super().__init__(methodName)
        if client is None:
            self.client = None
        else:
            self.client = client

    async def asyncSetUp(self):
        """
        Async setup method.
        """
        pass

    def clear_url_cache(self, url: str):
        """
        Clears the scraper cache for a specific URL.
        """
        cache_dir = Path(AppSettings.CACHE_DIR)
        if not cache_dir.exists():
            return

        domain = urlparse(url).netloc

        url_hash = hashlib.md5(url.encode()).hexdigest()

        domain_dir = cache_dir / domain
        if domain_dir.exists():
            for file in domain_dir.glob(f"{url_hash}.*"):
                try:
                    file.unlink()
                    print(f"Cleared cache file: {file}")
                except Exception as e:
                    print(f"Failed to delete cache file {file}: {e}")

    def clear_memory_cache(self):
        """
        Clears the memory cache for a specific user.
        """
        memory_dir = Path(AppSettings.MEMORY_DIR)
        if not memory_dir.exists():
            return

        assert self.bot.user is not None
        user_id = self.bot.user.id

        file_path = memory_dir / f"{user_id}.memory.json"
        if file_path.exists():
            try:
                file_path.unlink()
                print(f"Cleared memory cache file: {file_path}")
            except Exception as e:
                print(f"Failed to delete memory cache file {file_path}: {e}")

    def clear_summary_cache(self, channel_id: int, after_date: str, before_date: str):
        """
        Clears the summary tool cache for specific channel and timeframe.
        """
        cache_dir = Path(AppSettings.CACHE_DIR)
        if not cache_dir.exists():
            return

        filename = f"{channel_id}_{after_date}_{before_date}.json"
        file_path = cache_dir / filename

        if file_path.exists():
            try:
                file_path.unlink()
                print(f"Cleared summary cache file: {file_path}")
            except Exception as e:
                print(f"Failed to delete summary cache file {file_path}: {e}")

    async def asyncTearDown(self):
        """
        Async teardown method to be overridden by subclasses.
        """
        if self.client:
            await asyncio.sleep(2)
            dropped = self.client.clear_queue()
            if dropped > 0:
                print(f"[TestTearDown] Cleared {dropped} leftover messages from queue.")

        pass

    @property
    def bot(self) -> DummyClient:
        """
        Helper property to access the client, ensuring it's initialized.
        """
        if not self.client:
            raise RuntimeError("Client not initialized in TestCase")
        return self.client

    def get_resource_path(self, filename: str) -> str:
        """
        Returns the absolute path to a resource file.
        """
        base_path = os.path.join(os.getcwd(), "tests", "resources")
        return os.path.join(base_path, filename)

    async def send_image(
        self, filename: str = "image.jpg", content: str = ""
    ) -> discord.Message:
        """
        Helper to send an image from resources.
        """
        path = self.get_resource_path(filename)
        if not os.path.exists(path):
            if filename == "test_image.png":
                path = self.get_resource_path("image.jpg")

        if not os.path.exists(path):
            raise FileNotFoundError(f"Resource {filename} not found at {path}")

        with open(path, "rb") as f:
            file = discord.File(f, filename=filename)
            return await self.bot.send_and_wait(content, files=[file])

    async def send_video(
        self, filename: str = "video.mp4", content: str = ""
    ) -> discord.Message:
        """
        Helper to send a video from resources.
        """
        path = self.get_resource_path(filename)
        if not os.path.exists(path):
            if filename == "test_video.mp4":
                path = self.get_resource_path("video.mp4")

        if not os.path.exists(path):
            raise FileNotFoundError(f"Resource {filename} not found at {path}")

        with open(path, "rb") as f:
            file = discord.File(f, filename=filename)
            return await self.bot.send_and_wait(content, files=[file])

    def assertHasAudioAttachment(self, response: discord.Message) -> None:
        """
        Asserts that the response message contains at least one audio attachment.
        """
        has_audio = False
        if response.attachments:
            for att in response.attachments:
                if (
                    att.filename.endswith(".mp3")
                    or att.filename.endswith(".ogg")
                    or att.filename.endswith(".wav")
                ):
                    has_audio = True
                    break

        self.assertTrue(
            has_audio, "Expected audio attachment (.mp3, .ogg, .wav), but none found."
        )

    async def get_test_guild_channel(self):
        """
        Returns the guild channel for testing, ensuring it's valid.
        """
        channel = self.bot.get_channel(self.bot.channel_id)
        if not channel:
            channel = await self.bot.fetch_channel(self.bot.channel_id)

        if not isinstance(
            channel, (discord.TextChannel, discord.VoiceChannel, discord.StageChannel)
        ):
            raise TypeError("Test channel must be a guild channel")

        return channel

    def get_memory_file(self) -> Path:
        """
        Returns the path to the current user's memory file.
        """
        assert self.bot.user is not None
        memory_dir = Path(AppSettings.MEMORY_DIR)
        return memory_dir / f"{self.bot.user.id}.memory.json"
