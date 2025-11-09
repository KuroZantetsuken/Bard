import logging
from typing import List, Tuple

import discord

from settings import Settings

log = logging.getLogger("Bard")


class ReplyChainConstructor:
    """
    Constructs a formatted string and gathers attachments from a chain of Discord message replies.
    """

    def __init__(self, max_depth: int = Settings.MAX_REPLY_DEPTH):
        """
        Initializes the ReplyChainConstructor.

        Args:
            max_depth: The maximum number of messages to traverse in the reply chain.
        """
        log.debug("Initializing ReplyChainConstructor", extra={"max_depth": max_depth})
        self.max_depth = max_depth

    async def build_reply_chain(
        self, message: discord.Message
    ) -> Tuple[str, List[discord.Attachment]]:
        """
        Traverses a chain of replies, formats them into a single string, and collects their attachments.

        Args:
            message: The starting Discord message in the reply chain.

        Returns:
            A tuple containing:
            - A formatted string representing the conversation in the reply chain.
            - A list of discord.Attachment objects from the reply chain.
        """
        log.debug("Building reply chain", extra={"message_id": message.id})
        if not message.reference or not message.reference.message_id:
            return "", []

        chain = []
        attachments = []

        current_message = message
        for i in range(self.max_depth):
            if (
                not current_message.reference
                or not current_message.reference.message_id
            ):
                break

            try:
                log.debug(
                    f"Fetching replied message {i + 1}/{self.max_depth}",
                    extra={"message_id": current_message.reference.message_id},
                )
                replied_msg = await current_message.channel.fetch_message(
                    current_message.reference.message_id
                )
                chain.append(replied_msg)

                if replied_msg.attachments:
                    attachments.extend(replied_msg.attachments)

                current_message = replied_msg
            except discord.NotFound:
                log.warning(
                    f"Could not find replied message with ID: {current_message.reference.message_id}"
                )
                break

        if not chain:
            return "", []

        chain.reverse()

        formatted_chain = ["[REPLY_CHAIN:START]"]
        for msg in chain:
            formatted_chain.append(f"<{msg.author.name}>: {msg.content}")
        formatted_chain.append("[REPLY_CHAIN:END]")

        final_chain = "\n".join(formatted_chain)
        log.info(f"Built reply chain of depth {len(chain)}.")
        log.debug(
            "Finished building reply chain",
            extra={
                "chain": final_chain,
                "attachments_count": len(attachments),
            },
        )
        return final_chain, attachments
