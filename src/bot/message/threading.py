import asyncio
import logging
from typing import List, Optional

import discord

from ai.chat.titler import ThreadTitler
from settings import Settings

log = logging.getLogger("Bard")


class ThreadManager:
    """
    Manages the creation and naming of threads for long bot responses.
    """

    def __init__(self, thread_titler: ThreadTitler):
        """
        Initializes the ThreadManager.

        Args:
            thread_titler: The service for generating thread titles.
        """
        self.thread_titler = thread_titler
        self.max_message_length = Settings.MAX_DISCORD_MESSAGE_LENGTH
        log.debug("ThreadManager initialized.")

    async def create_thread_if_needed(
        self,
        message_to_reply_to: discord.Message,
        text_content: str,
        send_func,
        split_func,
    ) -> Optional[List[discord.Message]]:
        """
        Creates a thread for a long message, sends the initial part of the message,
        and then sends the rest of the content in the new thread.

        Args:
            message_to_reply_to: The original message to reply to.
            text_content: The full text content of the reply.
            send_func: A function to send a single message.
            split_func: A function to split a message into chunks.

        Returns:
            A list of sent messages, or None if a thread was not created.
        """

        should_create_thread = len(text_content) > self.max_message_length

        if not should_create_thread:
            log.debug("Message is not long enough to require a thread.")
            return None

        if not isinstance(
            message_to_reply_to.channel,
            (
                discord.TextChannel,
                discord.ForumChannel,
                discord.VoiceChannel,
                discord.Thread,
            ),
        ):
            log.debug(
                "Cannot create thread in this channel type, will send as chunks.",
                extra={"channel_type": type(message_to_reply_to.channel).__name__},
            )
            return None

        first_sentence_end = text_content.find(". ")
        if first_sentence_end == -1 or first_sentence_end > self.max_message_length:
            log.debug("No suitable split point found for thread creation.")
            return None

        first_part = text_content[: first_sentence_end + 1]
        rest_of_content = text_content[first_sentence_end + 2 :].strip()
        sent_messages = []

        first_message = await send_func(
            message_to_reply_to.channel,
            first_part,
            reply_to=message_to_reply_to,
        )

        if not first_message:
            log.error("Failed to send the initial message to start a thread.")
            return []

        sent_messages.append(first_message)
        log.debug(
            "Initial message sent, preparing to create thread.",
            extra={"message_id": first_message.id},
        )

        try:
            thread = await first_message.create_thread(
                name="Continuation of your request..."
            )
            log.debug(
                "Created thread with a placeholder name.",
                extra={"thread_id": thread.id, "placeholder_name": thread.name},
            )

            async def update_thread_title():
                log.debug(
                    "Starting background task to update thread title.",
                    extra={"thread_id": thread.id},
                )
                try:
                    new_title = await self.thread_titler.generate_title(text_content)
                    if new_title and new_title.strip():
                        await thread.edit(name=new_title.strip()[:100])
                        log.info(
                            "Successfully updated thread title.",
                            extra={"thread_id": thread.id, "new_title": new_title},
                        )
                    else:
                        log.warning(
                            "Thread title generation returned an empty title. Keeping placeholder.",
                            extra={"thread_id": thread.id},
                        )
                except Exception as e:
                    log.error(
                        "Failed to update thread title.",
                        extra={"thread_id": thread.id, "error": e},
                        exc_info=True,
                    )
                log.debug(
                    "Finished thread title update task.", extra={"thread_id": thread.id}
                )

            asyncio.create_task(update_thread_title())

            if rest_of_content:
                thread_chunks = split_func(rest_of_content)
                for chunk in thread_chunks:
                    sent_thread_msg = await send_func(thread, chunk)
                    if sent_thread_msg:
                        sent_messages.append(sent_thread_msg)
        except discord.HTTPException as e:
            log.error(
                "Failed to create or send message to thread.",
                extra={"error": e},
                exc_info=True,
            )
            return sent_messages

        return sent_messages
