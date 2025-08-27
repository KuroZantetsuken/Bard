import logging
from typing import Optional

import aiohttp
import discord

from config import Config
from utilities.files import create_temp_file

# Initialize logger for the voice module.
logger = logging.getLogger("Bard")


class VoiceMessageSender:
    """
    A service for sending native voice messages to Discord using direct API interaction.
    """

    def __init__(self, bot_token: str, logger: logging.Logger):
        """
        Initializes the VoiceMessageSender.

        Args:
            bot_token: The Discord bot token for API authentication.
            logger: The logger instance for diagnostics.
        """
        self.bot_token = bot_token
        self.logger = logger
        self.voice_message_flag = Config.DISCORD_VOICE_MESSAGE_FLAG

    async def _send_native_voice_message(
        self,
        message_to_reply_to: discord.Message,
        audio_data: bytes,
        duration_secs: float,
        waveform_b64: str,
    ) -> Optional[discord.Message]:
        """
        Attempts to send a native Discord voice message using Discord's API.

        Args:
            message_to_reply_to: The original message to reply to.
            audio_data: The raw audio data in bytes (OGG format).
            duration_secs: The duration of the audio in seconds.
            waveform_b64: The base64 encoded waveform of the audio.

        Returns:
            The sent Discord Message object if successful, None otherwise.
        """
        try:
            async with create_temp_file(audio_data, ".ogg") as temp_audio_path:
                async with aiohttp.ClientSession() as session:
                    channel_id = str(message_to_reply_to.channel.id)
                    upload_url = (
                        f"https://discord.com/api/v10/channels/{channel_id}/attachments"
                    )
                    headers = {
                        "Authorization": f"Bot {self.bot_token}",
                        "Content-Type": "application/json",
                    }
                    payload = {
                        "files": [
                            {
                                "filename": "voice_message.ogg",
                                "file_size": len(audio_data),
                                "id": "0",
                                "is_clip": False,
                            }
                        ]
                    }
                    async with session.post(
                        upload_url, json=payload, headers=headers
                    ) as resp:
                        resp.raise_for_status()
                        data = await resp.json()
                        attachment = data["attachments"][0]

                    async with session.put(
                        attachment["upload_url"],
                        data=open(temp_audio_path, "rb"),
                        headers={"Content-Type": "audio/ogg"},
                    ) as resp:
                        resp.raise_for_status()

                    send_url = (
                        f"https://discord.com/api/v10/channels/{channel_id}/messages"
                    )
                    send_payload = {
                        "content": "",
                        "flags": self.voice_message_flag,
                        "attachments": [
                            {
                                "id": "0",
                                "filename": "voice_message.ogg",
                                "uploaded_filename": attachment["upload_filename"],
                                "duration_secs": duration_secs,
                                "waveform": waveform_b64,
                            }
                        ],
                        "message_reference": {
                            "message_id": str(message_to_reply_to.id)
                        },
                        "allowed_mentions": {"parse": [], "replied_user": False},
                    }
                    async with session.post(
                        send_url, json=send_payload, headers=headers
                    ) as resp:
                        resp.raise_for_status()
                        data = await resp.json()
                        sent_audio_message = (
                            await message_to_reply_to.channel.fetch_message(data["id"])
                        )
                        return sent_audio_message
        except aiohttp.ClientResponseError as e:
            self.logger.error(
                f"HTTP error sending native voice message: Status {e.status}, Response: {e.message}.",
                exc_info=True,
            )
            return None
        except Exception as e:
            self.logger.error(
                f"Error sending native voice message: {e}.", exc_info=True
            )
            return None
