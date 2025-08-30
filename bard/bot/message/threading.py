import asyncio
import logging
from typing import List, Optional

import discord

from bard.ai.chat.titler import ThreadTitler
from config import Config


class ThreadManager:
    """
    Manages the creation and naming of threads for long bot responses.
    """

    def __init__(self, logger: logging.Logger, thread_titler: ThreadTitler):
        """
        Initializes the ThreadManager.

        Args:
            logger: The logger instance for diagnostics.
            thread_titler: The service for generating thread titles.
        """
        self.logger = logger
        self.thread_titler = thread_titler
        self.max_message_length = Config.MAX_DISCORD_MESSAGE_LENGTH

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
            return None

        first_sentence_end = text_content.find(". ")
        if first_sentence_end == -1 or first_sentence_end > self.max_message_length:
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
            self.logger.error("Failed to send the initial message to start a thread.")
            return []

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
                thread_chunks = split_func(rest_of_content)
                for chunk in thread_chunks:
                    sent_thread_msg = await send_func(thread, chunk)
                    if sent_thread_msg:
                        sent_messages.append(sent_thread_msg)
        except discord.HTTPException as e:
            self.logger.error(f"Failed to create or send to thread: {e}.")

            return sent_messages

        return sent_messages
