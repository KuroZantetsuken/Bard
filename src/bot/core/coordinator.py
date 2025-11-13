import logging
from typing import List, Optional, Tuple

from discord import Message, Reaction, User
from google.genai import errors as genai_errors

from ai.chat.conversation import AIConversation
from ai.chat.sessions import ChatSessionManager
from ai.types import FinalAIResponse
from bot.core.lifecycle import RequestManager
from bot.core.typing import TypingManager
from bot.message.parser import MessageParser
from bot.message.reactions import ReactionManager
from bot.message.sender import MessageSender
from bot.types import ParsedMessageContext, Request, RequestState
from scraper.orchestrator import ScrapingOrchestrator

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
        request_manager: RequestManager,
        reaction_manager: ReactionManager,
        scraping_orchestrator: ScrapingOrchestrator,
        typing_manager: TypingManager,
        chat_session_manager: ChatSessionManager,
    ):
        """
        Initializes the Coordinator with instances of its collaborating services.

        Args:
            message_parser: Service for parsing incoming Discord messages.
            ai_conversation: Service for managing AI conversational turns.
            message_sender: Service for sending messages back to Discord.
            request_manager: Service for managing request lifecycles.
            reaction_manager: Service for managing message reactions.
            scraping_orchestrator: Service for scraping URLs.
            typing_manager: Service for managing the typing indicator.
        """
        self.message_parser = message_parser
        self.ai_conversation = ai_conversation
        self.message_sender = message_sender
        self.request_manager = request_manager
        self.reaction_manager = reaction_manager
        self.scraping_orchestrator = scraping_orchestrator
        self.typing_manager = typing_manager
        self.chat_session_manager = chat_session_manager
        log.debug("Coordinator initialized with all services.")

    async def process(
        self,
        request: Request,
        bot_messages_to_edit: Optional[List[Message]] = None,
        reaction_to_remove: Optional[Tuple[Reaction, User]] = None,
    ) -> None:
        """
        Main orchestration method for processing a Discord message.
        This method encompasses the full flow from AI response generation
        and message sending, including error handling and reaction management.

        Args:
            request: The request object to process.
            bot_messages_to_edit: Optional list of bot messages that can be edited.
            reaction_to_remove: Optional tuple containing a Reaction and User to remove after processing.
        """
        if reaction_to_remove:
            await self.reaction_manager.remove_reaction(reaction_to_remove)

        message: Message = request.data["message"]
        log.info(
            "Starting message processing.",
            extra={
                "request_id": request.id,
                "message_id": message.id,
                "user_id": message.author.id,
            },
        )
        self.request_manager.update_request_state(request.id, RequestState.PROCESSING)
        final_ai_response: Optional[FinalAIResponse] = None
        bot_messages: Optional[List[Message]] = None
        try:
            self.typing_manager.start_typing(message.channel)

            if request.state == RequestState.CANCELLED:
                log.info(f"Request {request.id} was cancelled before processing.")
                return

            log.debug("Parsing message content.", extra={"message_id": message.id})
            parsed_context: ParsedMessageContext = await self.message_parser.parse(
                message
            )
            log.debug(
                "Message parsed successfully.",
                extra={"message_id": message.id, "context": parsed_context},
            )

            if request.state == RequestState.CANCELLED:
                log.info(f"Request {request.id} was cancelled after parsing.")
                return

            log.debug("Starting AI conversation.", extra={"message_id": message.id})
            chat_session = await self.chat_session_manager.get_or_create_session(
                message
            )
            final_ai_response = await self.ai_conversation.run(
                parsed_context, chat_session
            )
            log.debug(
                "AI conversation completed.",
                extra={"message_id": message.id, "response": final_ai_response},
            )

            if request.state == RequestState.CANCELLED:
                log.info(f"Request {request.id} was cancelled after AI conversation.")
                return

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
                request.data["bot_messages"] = bot_messages

            if final_ai_response:
                await self.reaction_manager.handle_request_completion(
                    request, final_ai_response.tool_emojis
                )

            self.request_manager.update_request_state(request.id, RequestState.DONE)

        except genai_errors.ServerError as e:
            self.request_manager.update_request_state(request.id, RequestState.ERROR)
            log.error(
                "Google API server error during message processing.",
                extra={
                    "request_id": request.id,
                    "message_id": message.id,
                    "error": str(e),
                },
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
                request.data["bot_messages"] = error_messages
                await self.reaction_manager.handle_request_error(request)

        except Exception as e:
            self.request_manager.update_request_state(request.id, RequestState.ERROR)
            log.error(
                "Unhandled error during message processing.",
                extra={
                    "request_id": request.id,
                    "message_id": message.id,
                    "error": str(e),
                },
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
                request.data["bot_messages"] = error_messages
                await self.reaction_manager.handle_request_error(request)
        finally:
            self.typing_manager.stop_typing(message.channel)
            log.info(
                "Finished message processing.",
                extra={"request_id": request.id, "message_id": message.id},
            )
