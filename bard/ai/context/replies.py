from typing import List, Tuple

import discord

from config import Config


class ReplyChainConstructor:
    """
    Constructs a formatted string and gathers attachments from a chain of Discord message replies.
    """

    def __init__(self, max_depth: int = Config.MAX_REPLY_DEPTH):
        """
        Initializes the ReplyChainConstructor.

        Args:
            max_depth: The maximum number of messages to traverse in the reply chain.
        """
        self.max_depth = max_depth

    async def build_reply_chain(
        self, message: discord.Message
    ) -> Tuple[str, List[bytes], List[str]]:
        """
        Traverses a chain of replies, formats them into a single string, and collects their attachments.

        Args:
            message: The starting Discord message in the reply chain.

        Returns:
            A tuple containing:
            - A formatted string representing the conversation in the reply chain.
            - A list of attachment data (bytes).
            - A list of attachment MIME types.
        """
        if not message.reference or not message.reference.message_id:
            return "", [], []

        chain = []
        attachments_data = []
        attachments_mime_types = []

        current_message = message
        for _ in range(self.max_depth):
            if (
                not current_message.reference
                or not current_message.reference.message_id
            ):
                break

            try:
                replied_msg = await current_message.channel.fetch_message(
                    current_message.reference.message_id
                )
                chain.append(replied_msg)

                if replied_msg.attachments:
                    for attachment in replied_msg.attachments:
                        attachments_data.append(await attachment.read())
                        attachments_mime_types.append(attachment.content_type)

                current_message = replied_msg
            except discord.NotFound:
                break

        if not chain:
            return "", [], []

        chain.reverse()

        formatted_chain = ["[REPLY_CHAIN:START]"]
        for msg in chain:
            formatted_chain.append(f"<{msg.author.name}>: {msg.content}")
        formatted_chain.append("[REPLY_CHAIN:END]")

        return "\n".join(formatted_chain), attachments_data, attachments_mime_types
