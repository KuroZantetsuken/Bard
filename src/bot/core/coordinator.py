import logging
from typing import List, Optional, Tuple

from discord import Message, Reaction, User
from google.genai import errors as genai_errors

from ai.chat.conversation import AIConversation
from ai.types import FinalAIResponse
from bot.lifecycle.tasks import TaskLifecycleManager
from bot.message.parser import MessageParser
from bot.message.reactions import ReactionManager
from bot.message.sender import MessageSender
from bot.types import ParsedMessageContext
from scraping.orchestrator import ScrapingOrchestrator

log = logging.getLogger("Bard")


class Coordinator:
    """
    Orchestrates the high-level workflow for processing a single Discord message.
    This class coordinates interactions between message parsing, AI conversation,
    message sending, and task lifecycle management.
    """

    def __init__(
        self,
        message_parser: MessageParser,
        ai_conversation: AIConversation,
        message_sender: MessageSender,
        task_lifecycle_manager: TaskLifecycleManager,
        scraping_orchestrator: ScrapingOrchestrator,
    ):
        """
        Initializes the Coordinator with instances of its collaborating services.

        Args:
            message_parser: Service for parsing incoming Discord messages.
            ai_conversation: Service for managing AI conversational turns.
            message_sender: Service for sending messages back to Discord.
            task_lifecycle_manager: Service for managing asynchronous task lifecycles.
            scraping_orchestrator: Service for scraping URLs.
        """
        self.message_parser = message_parser
        self.ai_conversation = ai_conversation
        self.message_sender = message_sender
        self.task_lifecycle_manager = task_lifecycle_manager
        self.reaction_manager = ReactionManager(self.message_sender.retry_emoji)
        self.scraping_orchestrator = scraping_orchestrator
        log.debug("Coordinator initialized with all services.")

    async def process(
        self,
        message: Message,
        bot_messages_to_edit: Optional[List[Message]] = None,
        reaction_to_remove: Optional[Tuple[Reaction, User]] = None,
    ) -> None:
        """
        Main orchestration method for processing a Discord message.
        This method encompasses the full flow from AI response generation
        and message sending, including error handling and reaction management.

        Args:
            message: The Discord message object to process.
            bot_messages_to_edit: Optional list of bot messages that can be edited.
            reaction_to_remove: Optional tuple containing a Reaction and User to remove after processing.
        """
        log.info(
            "Starting message processing.",
            extra={"message_id": message.id, "user_id": message.author.id},
        )
        final_ai_response: Optional[FinalAIResponse] = None
        bot_messages: Optional[List[Message]] = None
        try:
            log.debug("Entering typing context.", extra={"message_id": message.id})
            async with message.channel.typing():
                log.debug("Parsing message content.", extra={"message_id": message.id})
                parsed_context: ParsedMessageContext = await self.message_parser.parse(
                    message
                )
                log.debug(
                    "Message parsed successfully.",
                    extra={"message_id": message.id, "context": parsed_context},
                )

                log.debug("Starting AI conversation.", extra={"message_id": message.id})
                final_ai_response = await self.ai_conversation.run(parsed_context)
                log.debug(
                    "AI conversation completed.",
                    extra={"message_id": message.id, "response": final_ai_response},
                )

                log.debug("Sending AI response.", extra={"message_id": message.id})
                bot_messages = await self.message_sender.send(
                    message_to_reply_to=message,
                    text_content=final_ai_response.text_content,
                    existing_bot_messages_to_edit=bot_messages_to_edit,
                    **final_ai_response.media,
                    tool_emojis=final_ai_response.tool_emojis,
                )
                log.info(
                    "AI response sent.",
                    extra={
                        "message_id": message.id,
                        "bot_message_ids": [m.id for m in bot_messages]
                        if bot_messages
                        else [],
                    },
                )

                if bot_messages:
                    self.task_lifecycle_manager.active_bot_responses[message.id] = (
                        bot_messages
                    )
            log.debug("Exited typing context.", extra={"message_id": message.id})

            if bot_messages and final_ai_response:
                first_message = bot_messages[0]
                await self.reaction_manager.add_reactions(
                    first_message, final_ai_response.tool_emojis
                )

            if reaction_to_remove:
                await self.reaction_manager.remove_reaction(reaction_to_remove)

        except genai_errors.ServerError as e:
            log.error(
                "Google API server error during message processing.",
                extra={"message_id": message.id, "error": str(e)},
                exc_info=True,
            )
            error_message = (
                "The model is currently overloaded. Please try again shortly."
            )
            error_messages = await self.message_sender.send(
                message,
                error_message,
                existing_bot_messages_to_edit=bot_messages_to_edit,
            )
            if error_messages:
                await self.reaction_manager.add_reactions(error_messages[0])

        except Exception as e:
            log.error(
                "Unhandled error during message processing.",
                extra={"message_id": message.id, "error": str(e)},
                exc_info=True,
            )
            error_message = (
                f"An error occurred while processing your request.\n```\n{e}\n```"
            )
            error_messages = await self.message_sender.send(
                message,
                error_message,
                existing_bot_messages_to_edit=bot_messages_to_edit,
            )
            if error_messages:
                await self.reaction_manager.add_reactions(error_messages[0])
        log.info(
            "Finished message processing.",
            extra={"message_id": message.id},
        )
