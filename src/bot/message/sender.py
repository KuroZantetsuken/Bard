import logging
import os
import tempfile
from typing import List, Optional, cast

import discord

from ai.chat.titler import ThreadTitler
from bot.message.manager import MessageManager
from bot.message.threading import ThreadManager
from bot.message.voice import VoiceMessageSender
from settings import Settings

log = logging.getLogger("Bard")


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
        thread_titler: ThreadTitler,
    ):
        """
        Initializes the MessageSender service.

        Args:
            bot_token: Discord bot token for API authentication.
            retry_emoji: The emoji used to trigger a retry reaction.
            cancel_emoji: The emoji used to cancel a response generation.
            thread_titler: The service for generating thread titles.
        """
        self.bot_token = bot_token
        self.retry_emoji = retry_emoji
        self.cancel_emoji = cancel_emoji
        self.thread_manager = ThreadManager(thread_titler)
        self.max_message_length = Settings.MAX_DISCORD_MESSAGE_LENGTH
        self.voice_sender = VoiceMessageSender(bot_token)
        self.message_manager = MessageManager()
        log.debug("MessageSender initialized.")

    def _split_long_paragraph(self, paragraph: str) -> List[str]:
        """
        Splits a single long paragraph into chunks, attempting to avoid splitting
        inside a masked markdown URL.
        """
        chunks = []
        remaining_text = paragraph

        while len(remaining_text) > self.max_message_length:
            split_pos = self.max_message_length

            best_split_pos = remaining_text.rfind(" ", 0, split_pos)
            if best_split_pos != -1:
                split_pos = best_split_pos

            last_open_bracket = remaining_text.rfind("[", 0, split_pos)
            if last_open_bracket != -1:
                next_close_bracket = remaining_text.find("]", last_open_bracket)
                if next_close_bracket != -1 and next_close_bracket < split_pos:
                    if (
                        remaining_text[next_close_bracket + 1 : next_close_bracket + 2]
                        == "("
                    ):
                        link_end = remaining_text.find(")", next_close_bracket)
                        if (
                            link_end != -1
                            and split_pos > last_open_bracket
                            and split_pos < link_end
                        ):
                            if last_open_bracket > 0:
                                split_pos = last_open_bracket

            if last_open_bracket != -1:
                last_close_bracket_before_split = remaining_text.rfind(
                    "]", 0, split_pos
                )
                if last_open_bracket > last_close_bracket_before_split:
                    if last_open_bracket > 0:
                        split_pos = last_open_bracket

            if split_pos == 0:
                split_pos = self.max_message_length

            chunks.append(remaining_text[:split_pos])
            remaining_text = remaining_text[split_pos:].lstrip()

        if remaining_text:
            chunks.append(remaining_text)

        return chunks

    def _split_message_into_chunks(self, text_content: str) -> List[str]:
        """
        Splits a long text message into chunks that fit Discord's message length limit.
        It attempts to split by paragraphs first to maintain readability and avoids
        splitting masked markdown URLs.
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

                if len(paragraph_to_add) > self.max_message_length:
                    sub_chunks = self._split_long_paragraph(paragraph_to_add)
                    if sub_chunks:
                        chunks.extend(sub_chunks[:-1])
                        current_chunk = sub_chunks[-1]
                else:
                    current_chunk = paragraph_to_add

        if current_chunk.strip():
            chunks.append(current_chunk.strip())

        if not chunks and text_content:
            chunks.extend(self._split_long_paragraph(text_content))

        return [chunk for chunk in chunks if chunk]

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
            log.error(
                "Failed to send message reply, falling back to channel.",
                extra={"error": e},
            )

            try:
                return await channel.send(content, files=files_to_send)
            except discord.HTTPException as e_chan:
                log.error(
                    "Failed to send message to channel directly.",
                    extra={"error": e_chan},
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

        if files_to_attach and len(text_content) > self.max_message_length:
            log.warning(
                "Text content truncated due to attachment and length limits.",
                extra={"original_length": len(text_content)},
            )
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
                cast(discord.abc.Messageable, message_to_reply_to.channel),
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
        Sends a text reply. If the reply is long and has no attachments, it delegates to the ThreadManager
        to create a thread. Otherwise, it sends the reply as chunked messages.

        Args:
            message_to_reply_to: The original message to reply to.
            text_content: The text content of the reply.
            files_to_attach: Optional list of Discord File objects to attach.

        Returns:
            A list of sent Discord Message objects.
        """
        if not text_content or not text_content.strip():
            log.debug("No text content provided, using default message.")
            text_content = (
                "I processed your request but have no further text to add."
                if not files_to_attach
                else ""
            )

        if files_to_attach:
            log.debug("Sending reply with attachments as chunks.")
            return await self._send_reply_as_chunks(
                message_to_reply_to, text_content, files_to_attach
            )

        log.debug("Attempting to create a thread for a long message.")
        thread_messages = await self.thread_manager.create_thread_if_needed(
            message_to_reply_to,
            text_content,
            self._send_single_message_with_fallback,
            self._split_message_into_chunks,
        )

        if thread_messages is not None:
            log.debug(
                "Thread created for response.",
                extra={"message_count": len(thread_messages)},
            )
            if len(thread_messages) == 1 and text_content.find(". ") != -1:
                first_sentence_end = text_content.find(". ")
                rest_of_content = text_content[first_sentence_end + 2 :].strip()
                if rest_of_content:
                    log.debug(
                        "Sending remaining content as fallback chunks.",
                        extra={"remaining_length": len(rest_of_content)},
                    )
                    fallback_messages = await self._send_reply_as_chunks(
                        message_to_reply_to, rest_of_content
                    )
                    thread_messages.extend(fallback_messages)
            return thread_messages

        log.debug("Sending reply as chunks without creating a thread.")
        return await self._send_reply_as_chunks(
            message_to_reply_to, text_content, files_to_attach
        )

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
            log.warning("No content provided to send. Skipping message.")
            return []

        all_sent_messages: List[discord.Message] = []

        if existing_bot_messages_to_edit:
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
                    log.debug(
                        "Safely editing existing message.",
                        extra={"message_id": existing_bot_messages_to_edit[0].id},
                    )
                    edited_message = await existing_bot_messages_to_edit[0].edit(
                        content=text_content
                    )
                    all_sent_messages.append(edited_message)

                    return all_sent_messages
                except discord.HTTPException as e:
                    log.warning(
                        "Failed to edit message, will delete and resend.",
                        extra={"error": e},
                    )

            first_message_to_edit = existing_bot_messages_to_edit[0]
            if first_message_to_edit.thread:
                try:
                    log.debug(
                        "Deleting old thread starter message.",
                        extra={"message_id": first_message_to_edit.id},
                    )
                    await self.message_manager.delete_message(first_message_to_edit)
                except discord.HTTPException as e:
                    log.warning(
                        "Could not delete old thread starter message.",
                        extra={"error": e},
                    )
            else:
                for msg_to_delete in existing_bot_messages_to_edit:
                    try:
                        log.debug(
                            "Deleting old message.",
                            extra={"message_id": msg_to_delete.id},
                        )
                        await self.message_manager.delete_message(msg_to_delete)
                    except discord.HTTPException as e:
                        log.warning("Could not delete old message.", extra={"error": e})

        files_to_send: List[discord.File] = []
        temp_files_to_clean = []
        try:
            if image_data:
                log.debug("Processing image data for attachment.")
                with tempfile.NamedTemporaryFile(
                    suffix=".png", delete=False
                ) as temp_file:
                    temp_file.write(image_data)
                    temp_image_path = temp_file.name
                temp_files_to_clean.append(temp_image_path)
                filename = image_filename or "image.png"
                _, ext = os.path.splitext(filename)
                if not ext:
                    filename += ".png"
                files_to_send.append(discord.File(temp_image_path, filename=filename))
                log.debug(
                    "Image file prepared for sending.",
                    extra={"image_filename": filename},
                )

            if code_data:
                log.debug("Processing code data for attachment.")
                with tempfile.NamedTemporaryFile(
                    suffix=".py", delete=False
                ) as temp_file:
                    temp_file.write(code_data)
                    temp_code_path = temp_file.name
                temp_files_to_clean.append(temp_code_path)
                filename = code_filename or "code.py"
                _, ext = os.path.splitext(filename)
                if not ext:
                    filename += ".py"
                files_to_send.append(discord.File(temp_code_path, filename=filename))
                log.debug(
                    "Code file prepared for sending.", extra={"code_filename": filename}
                )

            if text_content or files_to_send:
                text_content_for_send = text_content or ""
                log.debug(
                    "Sending text reply with attachments.",
                    extra={"file_count": len(files_to_send)},
                )
                messages = await self._send_text_reply(
                    message_to_reply_to, text_content_for_send, files_to_send
                )
                all_sent_messages.extend(messages)
        finally:
            log.debug(
                "Cleaning up temporary files.",
                extra={"file_count": len(temp_files_to_clean)},
            )
            for path in temp_files_to_clean:
                if os.path.exists(path):
                    os.unlink(path)

        if audio_data:
            log.debug("Processing audio data for sending.")
            sent_audio_message = await self.voice_sender._send_native_voice_message(
                message_to_reply_to,
                audio_data,
                duration_secs,
                waveform_b64 if waveform_b64 is not None else "",
            )
            if sent_audio_message:
                log.info("Successfully sent native voice message.")
                all_sent_messages.append(sent_audio_message)
            else:
                log.warning(
                    "Failed to send native voice message, falling back to file."
                )
                temp_audio_path = None
                try:
                    with tempfile.NamedTemporaryFile(
                        suffix=".ogg", delete=False
                    ) as temp_file:
                        temp_file.write(audio_data)
                        temp_audio_path = temp_file.name
                    file = discord.File(temp_audio_path, filename="voice_response.ogg")
                    sent_msg = await self._send_single_message_with_fallback(
                        cast(discord.abc.Messageable, message_to_reply_to.channel),
                        content="",
                        files=[file],
                        reply_to=message_to_reply_to,
                    )
                    if sent_msg:
                        all_sent_messages.append(sent_msg)
                except Exception as e:
                    log.error("Failed to send audio fallback.", extra={"error": e})
                finally:
                    if temp_audio_path and os.path.exists(temp_audio_path):
                        os.unlink(temp_audio_path)

        if not all_sent_messages and any(
            [text_content, audio_data, image_data, code_data]
        ):
            log.error("All content sending attempts failed.")

        log.debug(
            "Message sending process complete.",
            extra={"sent_message_count": len(all_sent_messages)},
        )
        return all_sent_messages
