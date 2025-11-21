import logging
import os
import tempfile
from typing import Optional

import aiohttp
import discord

from settings import Settings

log = logging.getLogger("Bard")


class VoiceMessageSender:
    """
    A service for sending native voice messages to Discord using direct API interaction.
    """

    def __init__(self, bot_token: str):
        """
        Initializes the VoiceMessageSender.

        Args:
            bot_token: The Discord bot token for API authentication.
        """
        self.bot_token = bot_token
        self.voice_message_flag = Settings.DISCORD_VOICE_MESSAGE_FLAG
        log.debug("VoiceMessageSender initialized.")

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
        temp_audio_path = None
        try:
            log.debug("Creating temporary file for voice message.")
            with tempfile.NamedTemporaryFile(suffix=".ogg", delete=False) as temp_file:
                temp_file.write(audio_data)
                temp_audio_path = temp_file.name

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
                log.debug("Requesting voice message upload URL.")
                async with session.post(
                    upload_url, json=payload, headers=headers
                ) as resp:
                    resp.raise_for_status()
                    data = await resp.json()
                    attachment = data["attachments"][0]
                log.debug("Upload URL received, uploading audio data.")
                async with session.put(
                    attachment["upload_url"],
                    data=open(temp_audio_path, "rb"),
                    headers={"Content-Type": "audio/ogg"},
                ) as resp:
                    resp.raise_for_status()
                log.debug("Audio data uploaded, sending message.")
                send_url = f"https://discord.com/api/v10/channels/{channel_id}/messages"
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
                    "message_reference": {"message_id": str(message_to_reply_to.id)},
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
                    log.info(
                        "Successfully sent native voice message.",
                        extra={"message_id": sent_audio_message.id},
                    )
                    return sent_audio_message
        except aiohttp.ClientResponseError as e:
            log.error(
                "HTTP error sending native voice message.",
                extra={"status": e.status, "error_message": e.message},
                exc_info=True,
            )
            return None
        except Exception as e:
            log.error(
                "An unexpected error occurred while sending native voice message.",
                extra={"error": e},
                exc_info=True,
            )
            return None
        finally:
            if temp_audio_path and os.path.exists(temp_audio_path):
                try:
                    log.debug(f"Cleaning up temporary audio file: {temp_audio_path}")
                    os.unlink(temp_audio_path)
                except OSError as e:
                    log.error(
                        f"Failed to clean up temporary audio file: {temp_audio_path}",
                        extra={"error": e},
                    )
