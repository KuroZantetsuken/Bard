import asyncio
import logging
import os
import tempfile
from contextlib import asynccontextmanager
from typing import AsyncIterator, List, Optional, Union

import aiohttp
import discord

from ai.titler import ThreadTitler
from config import Config

# Initialize logger for the sender module.
logger = logging.getLogger("Bard")


@asynccontextmanager
async def _create_temp_file(data: bytes, suffix: str) -> AsyncIterator[str]:
    """
    Asynchronously creates a temporary file, writes data to it, and ensures it's cleaned up
    upon exiting the context.

    Args:
        data: The bytes data to write to the temporary file.
        suffix: The file extension (e.g., ".png", ".py", ".ogg").

    Yields:
        The path to the created temporary file.
    """
    temp_path = None
    try:
        with tempfile.NamedTemporaryFile(suffix=suffix, delete=False) as temp_file:
            temp_path = temp_file.name
            temp_file.write(data)
        yield temp_path
    finally:
        if temp_path and os.path.exists(temp_path):
            try:
                os.unlink(temp_path)
            except OSError as e:
                logger.warning(f"Could not delete temporary file {temp_path}: {e}")


class MessageSender:
    """
    A service for sending messages to Discord, handling text splitting, attachments,
    and native voice messages. It consolidates all Discord message sending logic.
    """

    def __init__(
        self,
        bot_token: str,
        retry_emoji: str,
        cancel_emoji: str,
        logger: logging.Logger,
        thread_titler: ThreadTitler,
    ):
        """
        Initializes the MessageSender service.

        Args:
            bot_token: Discord bot token for API authentication.
            retry_emoji: The emoji used to trigger a retry reaction.
            cancel_emoji: The emoji used to cancel a response generation.
            logger: The configured logger instance for diagnostics.
            thread_titler: The service for generating thread titles.
        """
        self.bot_token = bot_token
        self.retry_emoji = retry_emoji
        self.cancel_emoji = cancel_emoji
        self.logger = logger
        self.thread_titler = thread_titler
        self.max_message_length = Config.MAX_DISCORD_MESSAGE_LENGTH
        self.voice_message_flag = Config.DISCORD_VOICE_MESSAGE_FLAG

    def _split_message_into_chunks(self, text_content: str) -> List[str]:
        """
        Splits a long text message into chunks that fit Discord's message length limit.
        It attempts to split by paragraphs first to maintain readability.

        Args:
            text_content: The full text content to split.

        Returns:
            A list of strings, where each string is a message chunk.
        """
        if len(text_content) <= self.max_message_length:
            return [text_content]

        chunks = []
        current_chunk = ""
        paragraphs = text_content.split("\n\n")

        for i, paragraph in enumerate(paragraphs):
            paragraph_to_add = paragraph + ("\n\n" if i < len(paragraphs) - 1 else "")
            if len(current_chunk) + len(paragraph_to_add) <= self.max_message_length:
                current_chunk += paragraph_to_add
            else:
                if current_chunk:
                    chunks.append(current_chunk.strip())
                    current_chunk = ""
                # If a single paragraph is too long, split it character by character.
                if len(paragraph_to_add) > self.max_message_length:
                    for k in range(0, len(paragraph_to_add), self.max_message_length):
                        chunks.append(paragraph_to_add[k : k + self.max_message_length])
                else:
                    current_chunk = paragraph_to_add
        if current_chunk.strip():
            chunks.append(current_chunk.strip())

        if not chunks:
            # Fallback for extreme edge cases where splitting might result in an empty list.
            chunks = [text_content[: self.max_message_length]]

        return chunks

    async def _send_single_message_with_fallback(
        self,
        channel: discord.abc.Messageable,
        content: str,
        files: Optional[List[discord.File]] = None,
        reply_to: Optional[discord.Message] = None,
    ) -> Optional[discord.Message]:
        """
        Sends a single message, with a fallback to sending to the channel directly if replying fails.

        Args:
            channel: The Discord channel to send the message to.
            content: The text content of the message.
            files: Optional list of Discord File objects to attach.
            reply_to: Optional Discord Message object to reply to.

        Returns:
            The sent Discord Message object, or None if sending failed.
        """
        files_to_send = files if files is not None else []
        try:
            if reply_to:
                return await reply_to.reply(content, files=files_to_send)
            else:
                return await channel.send(content, files=files_to_send)
        except discord.HTTPException as e:
            self.logger.error(f"Failed to send message: {e}.")
            # Fallback to sending to the channel directly if replying failed.
            try:
                return await channel.send(content, files=files_to_send)
            except discord.HTTPException as e_chan:
                self.logger.error(
                    f"Failed to send message to channel directly: {e_chan}."
                )
                return None

    async def _send_reply_as_chunks(
        self,
        message_to_reply_to: discord.Message,
        text_content: str,
        files_to_attach: Optional[List[discord.File]] = None,
    ) -> List[discord.Message]:
        """
        Sends a text reply, splitting into multiple messages if necessary to adhere to Discord's limits.
        Attaches files only to the first message chunk. This is the fallback for messages that
        are not sent in a thread.

        Args:
            message_to_reply_to: The original message to reply to.
            text_content: The text content of the reply.
            files_to_attach: Optional list of Discord File objects to attach.

        Returns:
            A list of sent Discord Message objects.
        """
        sent_messages: List[discord.Message] = []

        # Handle case where attachments prevent splitting or text is too long.
        if files_to_attach and len(text_content) > self.max_message_length:
            warning_msg = "\n\n[Warning: Response truncated. The full response was too long to display with attachments.]"
            text_content = (
                text_content[: self.max_message_length - len(warning_msg)] + warning_msg
            )
            chunks = [text_content]
        else:
            chunks = self._split_message_into_chunks(text_content)

        for i, chunk in enumerate(chunks):
            files_for_this_turn = files_to_attach if i == 0 else None
            sent_msg = await self._send_single_message_with_fallback(
                message_to_reply_to.channel,
                chunk,
                files=files_for_this_turn,
                reply_to=message_to_reply_to if i == 0 else None,
            )
            if sent_msg:
                sent_messages.append(sent_msg)

        return sent_messages

    async def _send_text_reply(
        self,
        message_to_reply_to: discord.Message,
        text_content: str,
        files_to_attach: Optional[List[discord.File]] = None,
    ) -> List[discord.Message]:
        """
        Sends a text reply. If the reply is long and has no attachments, it splits it at the
        first sentence, creates a thread from the first part, and sends subsequent parts in the thread.
        Otherwise, it sends the reply as chunked messages.

        Args:
            message_to_reply_to: The original message to reply to.
            text_content: The text content of the reply.
            files_to_attach: Optional list of Discord File objects to attach.

        Returns:
            A list of sent Discord Message objects, including the thread starter and thread messages.
        """
        sent_messages: List[discord.Message] = []
        if not text_content or not text_content.strip():
            text_content = (
                "I processed your request but have no further text to add."
                if not files_to_attach
                else ""
            )

        # A thread is created if the response is long and there are no attachments.
        should_create_thread = (
            len(text_content) > self.max_message_length and not files_to_attach
        )

        if not should_create_thread:
            return await self._send_reply_as_chunks(
                message_to_reply_to, text_content, files_to_attach
            )

        # Attempt to split at the first sentence.
        first_sentence_end = text_content.find(". ")
        if first_sentence_end == -1 or first_sentence_end > self.max_message_length:
            # Fallback if no period is found or the first sentence is too long.
            return await self._send_reply_as_chunks(
                message_to_reply_to, text_content, files_to_attach
            )

        first_part = text_content[: first_sentence_end + 1]
        rest_of_content = text_content[first_sentence_end + 2 :].strip()

        # Send the first part of the message to start the thread.
        first_message = await self._send_single_message_with_fallback(
            message_to_reply_to.channel,
            first_part,
            reply_to=message_to_reply_to,
        )

        if not first_message:
            self.logger.error("Failed to send the initial message to start a thread.")
            return []  # Cannot proceed if the first message fails.

        sent_messages.append(first_message)

        try:
            thread = await first_message.create_thread(
                name="Continuation of your request..."
            )
            self.logger.debug(
                f"Created thread '{thread.name}' ({thread.id}) with a placeholder name."
            )

            async def update_thread_title():
                self.logger.debug(
                    f"Starting thread title update for thread {thread.id}"
                )
                try:
                    new_title = await self.thread_titler.generate_title(text_content)
                    if new_title and new_title.strip():
                        await thread.edit(name=new_title.strip()[:100])
                        self.logger.debug(
                            f"Successfully updated thread '{thread.id}' name to '{new_title}'."
                        )
                    else:
                        self.logger.warning(
                            f"Thread title generation returned an empty title for thread {thread.id}. Keeping placeholder name."
                        )
                except Exception as e:
                    self.logger.error(
                        f"Failed to update thread title for thread {thread.id}: {e}",
                        exc_info=True,
                    )
                self.logger.debug(
                    f"Finished thread title update for thread {thread.id}"
                )

            asyncio.create_task(update_thread_title())

            if rest_of_content:
                thread_chunks = self._split_message_into_chunks(rest_of_content)
                for chunk in thread_chunks:
                    sent_thread_msg = await self._send_single_message_with_fallback(
                        thread, chunk
                    )
                    if sent_thread_msg:
                        sent_messages.append(sent_thread_msg)
        except discord.HTTPException as e:
            self.logger.error(f"Failed to create or send to thread: {e}.")
            # If thread fails, send remaining content as regular messages.
            if rest_of_content:
                fallback_messages = await self._send_reply_as_chunks(
                    message_to_reply_to, rest_of_content
                )
                sent_messages.extend(fallback_messages)

        return sent_messages

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
            async with _create_temp_file(audio_data, ".ogg") as temp_audio_path:
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

    async def send(
        self,
        message_to_reply_to: discord.Message,
        text_content: Optional[str],
        audio_data: Optional[bytes] = None,
        duration_secs: float = 0.0,
        waveform_b64: Optional[str] = None,
        image_data: Optional[bytes] = None,
        image_filename: Optional[str] = None,
        code_data: Optional[bytes] = None,
        code_filename: Optional[str] = None,
        existing_bot_messages_to_edit: Optional[List[discord.Message]] = None,
        tool_emojis: Optional[List[str]] = None,
    ) -> List[discord.Message]:
        """
        Sends a reply to a Discord message with optional text, audio, and image content.
        This method handles message editing, text splitting, file attachments,
        and native voice messages with fallback to audio file attachments.

        Args:
            message_to_reply_to: The original message to reply to.
            text_content: The text content to send (optional).
            audio_data: Audio data in bytes (optional).
            duration_secs: Duration of audio in seconds.
            waveform_b64: Base64 encoded waveform (optional).
            image_data: Image data in bytes (optional).
            image_filename: Filename for image attachment (optional).
            code_data: Python code data in bytes (optional).
            code_filename: Filename for code attachment (optional).
            existing_bot_messages_to_edit: Existing bot messages to edit (optional).
            tool_emojis: List of emojis representing tools used (optional).

        Returns:
            A list of sent Discord Message objects.
        """
        if not any([text_content, audio_data, image_data, code_data]):
            self.logger.warning("No content provided to send. Skipping message.")
            return []

        all_sent_messages: List[discord.Message] = []

        # Handle message editing.
        if existing_bot_messages_to_edit:
            # Allow editing only if it's a single text message without attachments or voice, and fits length.
            can_safely_edit = (
                len(existing_bot_messages_to_edit) == 1
                and text_content
                and not audio_data
                and not image_data
                and not code_data
                and not existing_bot_messages_to_edit[0].attachments
                and not existing_bot_messages_to_edit[0].flags.voice
                and len(text_content) <= self.max_message_length
            )
            if can_safely_edit:
                try:
                    edited_message = await existing_bot_messages_to_edit[0].edit(
                        content=text_content
                    )
                    all_sent_messages.append(edited_message)
                    # Reactions are added at the end, so return after this block.
                    return all_sent_messages
                except discord.HTTPException as e:
                    self.logger.warning(
                        f"Failed to edit message: {e}. Deleting for resend."
                    )

            # If editing is not safe, or failed, delete old messages before sending new ones.
            first_message_to_edit = existing_bot_messages_to_edit[0]
            if first_message_to_edit.thread:
                # If the message started a thread, only delete the starter message.
                try:
                    await first_message_to_edit.delete()
                except discord.HTTPException as e:
                    self.logger.warning(
                        f"Could not delete old thread starter message: {e}"
                    )
            else:
                # Otherwise, delete all associated messages.
                for msg_to_delete in existing_bot_messages_to_edit:
                    try:
                        await msg_to_delete.delete()
                    except discord.HTTPException as e:
                        self.logger.warning(f"Could not delete old message: {e}")

        # Prepare and send files (image, code).
        files_to_send: List[discord.File] = []
        if image_data:
            async with _create_temp_file(image_data, ".png") as temp_image_path:
                filename = image_filename or "image.png"
                _, ext = os.path.splitext(filename)
                if not ext:
                    filename += ".png"
                files_to_send.append(discord.File(temp_image_path, filename=filename))

        if code_data:
            async with _create_temp_file(code_data, ".py") as temp_code_path:
                filename = code_filename or "code.py"
                _, ext = os.path.splitext(filename)
                if not ext:
                    filename += ".py"
                files_to_send.append(discord.File(temp_code_path, filename=filename))

        if text_content or files_to_send:
            text_content_for_send = text_content or ""
            messages = await self._send_text_reply(
                message_to_reply_to, text_content_for_send, files_to_send
            )
            all_sent_messages.extend(messages)

        # Handle audio data.
        if audio_data:
            sent_audio_message = await self._send_native_voice_message(
                message_to_reply_to,
                audio_data,
                duration_secs,
                waveform_b64 if waveform_b64 is not None else "",
            )
            if sent_audio_message:
                all_sent_messages.append(sent_audio_message)
            else:
                # Fallback to sending audio as a file attachment if native voice message fails.
                try:
                    async with _create_temp_file(audio_data, ".ogg") as temp_audio_path:
                        file = discord.File(
                            temp_audio_path, filename="voice_response.ogg"
                        )
                        sent_msg = await self._send_single_message_with_fallback(
                            message_to_reply_to.channel,
                            content="",
                            files=[file],
                            reply_to=message_to_reply_to,
                        )
                        if sent_msg:
                            all_sent_messages.append(sent_msg)
                except Exception as e:
                    self.logger.error(f"Failed to send audio fallback: {e}")

        if not all_sent_messages and any(
            [text_content, audio_data, image_data, code_data]
        ):
            self.logger.error("All content sending attempts failed.")

        return all_sent_messages

    async def delete_message(self, message: Union[discord.Message, int]):
        """
        Deletes a Discord message.

        Args:
            message: The discord.Message object or the message ID (int) to delete.
        """
        try:
            if isinstance(message, int):
                self.logger.warning(
                    f"Cannot directly delete message by ID ({message}) without a discord.Message object or channel context. "
                    "Please provide a discord.Message object for reliable deletion."
                )
                return

            await message.delete()

        except discord.NotFound:
            self.logger.warning(
                f"Message not found, could not delete. ID: {message.id if isinstance(message, discord.Message) else message}."
            )
        except discord.Forbidden:
            self.logger.error(
                f"Bot lacks permissions to delete message ID: {message.id if isinstance(message, discord.Message) else message}."
            )
        except discord.HTTPException as e:
            self.logger.error(
                f"Error deleting message ID: {message.id if isinstance(message, discord.Message) else message}: {e}."
            )
        except Exception as e:
            self.logger.error(
                f"An unexpected error occurred while deleting message (ID: {message.id if isinstance(message, discord.Message) else message}): {e}"
            )

    async def remove_reaction(
        self, message: discord.Message, emoji: str, user: Optional[discord.User] = None
    ):
        """
        Removes a reaction from a Discord message.

        Args:
            message: The discord.Message object from which to remove the reaction.
            emoji: The emoji (str) to remove.
            user: Optional; The discord.User whose reaction to remove. If None, removes bot's own reaction.
        """
        try:
            if user:
                await message.remove_reaction(emoji, user)
            else:
                await message.remove_reaction(
                    emoji, message.author
                )  # Remove bot's own reaction.
        except discord.NotFound:
            self.logger.warning(
                f"Message or emoji not found, could not remove reaction '{emoji}' from message ID {message.id}."
            )
        except discord.Forbidden:
            self.logger.error(
                f"Bot lacks permissions to remove reaction '{emoji}' from message ID {message.id}."
            )
        except discord.HTTPException as e:
            self.logger.error(
                f"Error removing reaction '{emoji}' from message ID {message.id}: {e}."
            )
        except Exception as e:
            self.logger.error(
                f"Error removing reaction '{emoji}' from message ID {message.id}: {e}."
            )
