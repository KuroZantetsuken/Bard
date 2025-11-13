import asyncio
import logging
from dataclasses import dataclass, field
from datetime import datetime
from typing import Dict

import discord
from google.genai.chats import Chat

from ai.config import GeminiConfigManager
from ai.context.prompts import PromptBuilder
from ai.core import GeminiCore
from ai.tools.registry import ToolRegistry
from settings import Settings

log = logging.getLogger("Bard")


@dataclass
class ChatSession:
    """
    Represents a stateful chat session with the Gemini API, including metadata
    for session management and expiration.
    """

    chat: Chat
    root_message_id: int
    last_interaction: datetime = field(default_factory=datetime.utcnow)


class ChatSessionManager:
    """
    Manages the lifecycle of stateful Gemini Chat objects. It creates, stores,
    retrieves, and expires chat sessions based on message reply chains to maintain
    conversational context.
    """

    def __init__(
        self,
        settings: Settings,
        gemini_core: GeminiCore,
        prompt_builder: PromptBuilder,
        config_manager: GeminiConfigManager,
        tool_registry: ToolRegistry,
    ):
        self._sessions: Dict[int, ChatSession] = {}
        self._session_locks: Dict[int, asyncio.Lock] = {}
        self._settings = settings
        self._gemini_core = gemini_core
        self._prompt_builder = prompt_builder
        self._config_manager = config_manager
        self._tool_registry = tool_registry
        log.debug("ChatSessionManager initialized.")

    async def _get_session_key(self, message: discord.Message) -> int:
        """
        Determines the session key by traversing the reply chain. The key is the ID
        of the earliest message in the chain that is already associated with a session.
        If no existing session is found, the current message's ID becomes the new key.
        """
        current = message
        for _ in range(self._settings.MAX_REPLY_DEPTH):
            if current.id in self._sessions:
                return current.id
            if not current.reference or not current.reference.message_id:
                break
            try:
                current = await current.channel.fetch_message(
                    current.reference.message_id
                )
            except (discord.NotFound, discord.HTTPException):
                break
        return message.id

    async def get_or_create_session(self, message: discord.Message) -> Chat:
        """
        Retrieves an existing chat session or creates a new one for the given message.
        A lock is used to prevent race conditions during session creation.
        """
        session_key = await self._get_session_key(message)

        if session_key in self._sessions:
            session = self._sessions[session_key]
            session.last_interaction = datetime.utcnow()
            log.info(
                "Reusing existing chat session.",
                extra={"session_key": session_key, "message_id": message.id},
            )
            return session.chat

        if session_key not in self._session_locks:
            self._session_locks[session_key] = asyncio.Lock()

        async with self._session_locks[session_key]:
            if session_key in self._sessions:
                return self._sessions[session_key].chat

            log.info(
                "Creating new chat session.",
                extra={"session_key": session_key, "message_id": message.id},
            )
            tool_declarations = self._tool_registry.get_all_function_declarations()
            system_instruction = self._prompt_builder.system_prompt

            config = self._config_manager.create_config(
                system_instruction_str=system_instruction,
                tool_declarations=tool_declarations,
            )

            chat = self._gemini_core.client.chats.create(
                model=self._settings.MODEL_ID, config=config
            )

            new_session = ChatSession(chat=chat, root_message_id=session_key)
            self._sessions[session_key] = new_session

            if session_key in self._session_locks:
                del self._session_locks[session_key]

            return new_session.chat
