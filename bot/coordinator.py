import logging
from typing import List, Optional, Tuple

import discord
from discord import Message, Reaction, User

from ai.conversation import AIConversation
from ai.types import FinalAIResponse
from bot.commands import CommandHandler
from bot.parser import MessageParser
from bot.sender import MessageSender
from bot.types import ParsedMessageContext
from utilities.lifecycle import TaskLifecycleManager

# Initialize logger for the coordinator module.
logger = logging.getLogger("Bard")


class Coordinator:
    """
    Orchestrates the high-level workflow for processing a single Discord message.
    This class coordinates interactions between message parsing, AI conversation,
    message sending, command handling, and task lifecycle management.
    """

    def __init__(
        self,
        message_parser: MessageParser,
        ai_conversation: AIConversation,
        message_sender: MessageSender,
        command_handler: CommandHandler,
        task_lifecycle_manager: TaskLifecycleManager,
    ):
        """
        Initializes the Coordinator with instances of its collaborating services.

        Args:
            message_parser: Service for parsing incoming Discord messages.
            ai_conversation: Service for managing AI conversational turns.
            message_sender: Service for sending messages back to Discord.
            command_handler: Service for processing bot commands.
            task_lifecycle_manager: Service for managing asynchronous task lifecycles.
        """
        self.message_parser = message_parser
        self.ai_conversation = ai_conversation
        self.message_sender = message_sender
        self.command_handler = command_handler
        self.task_lifecycle_manager = task_lifecycle_manager

    async def process(
        self,
        message: Message,
        bot_messages_to_edit: Optional[List[Message]] = None,
        reaction_to_remove: Optional[Tuple[Reaction, User]] = None,
    ) -> None:
        """
        Main orchestration method for processing a Discord message.
        This method encompasses the full flow from command handling to AI response generation
        and message sending, including error handling and reaction management.

        Args:
            message: The Discord message object to process.
            bot_messages_to_edit: Optional list of bot messages that can be edited.
            reaction_to_remove: Optional tuple containing a Reaction and User to remove after processing.
        """
        logger.info(f"Processing message ID: {message.id} from user: {message.author}")
        final_ai_response: Optional[FinalAIResponse] = None
        bot_messages: Optional[List[Message]] = None
        try:
            logger.debug(f"Entering typing context for message ID: {message.id}")
            async with message.channel.typing():
                # Process command first. If a command is handled, stop further processing.
                was_handled, is_reset = await self.command_handler.process_command(
                    message,
                    message.guild.id if message.guild else None,
                    message.author.id,
                    bot_messages_to_edit,
                )
                if was_handled:
                    logger.info(
                        f"Command handled for message ID: {message.id}. No further AI processing."
                    )
                    return

                # Parse the incoming message into a structured context.
                logger.debug(f"Parsing message content for message ID: {message.id}")
                parsed_context: ParsedMessageContext = await self.message_parser.parse(
                    message
                )

                # Delegate to the AIConversation to generate a response.
                logger.debug(f"Starting AI conversation for message ID: {message.id}")
                final_ai_response = await self.ai_conversation.run(parsed_context)

                # Send the AI's response back to Discord.
                logger.debug(f"Sending AI response for message ID: {message.id}")
                bot_messages = await self.message_sender.send(
                    message_to_reply_to=message,
                    text_content=final_ai_response.text_content,
                    existing_bot_messages_to_edit=bot_messages_to_edit,
                    **final_ai_response.media,
                    tool_emojis=final_ai_response.tool_emojis,
                )

                # Update the TaskLifecycleManager with the new bot responses.
                if bot_messages:
                    self.task_lifecycle_manager.active_bot_responses[message.id] = (
                        bot_messages
                    )
            logger.debug(f"Exited typing context for message ID: {message.id}")

            # Add reactions to the first sent message.
            if bot_messages and final_ai_response:
                first_message = bot_messages[0]
                try:
                    await first_message.add_reaction(self.message_sender.retry_emoji)
                except discord.HTTPException as e:
                    logger.warning(
                        f"Could not add retry reaction to the first message {first_message.id}: {e}"
                    )
                if final_ai_response.tool_emojis:
                    for emoji in final_ai_response.tool_emojis:
                        try:
                            await first_message.add_reaction(emoji)
                        except discord.HTTPException as e:
                            logger.warning(
                                f"Could not add tool emoji reaction '{emoji}' to the first message {first_message.id}: {e}"
                            )

            # Remove the reaction if one was provided (e.g., a retry reaction).
            if reaction_to_remove:
                reaction, user = reaction_to_remove
                try:
                    await reaction.remove(user)
                except discord.HTTPException as e:
                    logger.warning(
                        f"Failed to remove reaction for message ID {message.id}: {e}"
                    )

        except Exception as e:
            logger.error(
                f"Unhandled error in process for message ID {message.id}: {e}",
                exc_info=True,
            )
            # Send an error message to the user if an unhandled exception occurs.
            await self.message_sender.send(
                message,
                "An error occurred while processing your request.",
                existing_bot_messages_to_edit=bot_messages_to_edit,
            )
