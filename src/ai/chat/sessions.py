import asyncio
import logging
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Dict, List, Optional

import discord
from google.genai import types as gemini_types
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
    leaf_message_id: int = 0


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

    async def _reconstruct_history(
        self, message: discord.Message
    ) -> List[gemini_types.Content]:
        """
        Reconstructs the chat history by walking up the Discord reply chain.
        Returns a list of gemini_types.Content.
        """
        history_messages = []
        current = message

        for _ in range(self._settings.MAX_REPLY_DEPTH):
            if not current.reference or not current.reference.message_id:
                break

            try:
                parent = await current.channel.fetch_message(
                    current.reference.message_id
                )
                history_messages.append(parent)
                current = parent
            except (discord.NotFound, discord.HTTPException):
                break

        history_messages.reverse()

        gemini_history: List[gemini_types.Content] = []
        for msg in history_messages:
            role = "model" if msg.author.bot else "user"
            parts = [gemini_types.Part(text=msg.content)]
            gemini_history.append(gemini_types.Content(role=role, parts=parts))

        return gemini_history

    async def get_or_create_session(self, message: discord.Message) -> Chat:
        """
        Retrieves an existing chat session or creates a new one for the given message.
        A lock is used to prevent race conditions during session creation.
        """
        session_key = await self._get_session_key(message)

        session_to_use: Optional[ChatSession] = None
        is_branch = False

        if session_key in self._sessions:
            existing_session = self._sessions[session_key]

            ref_id = message.reference.message_id if message.reference else None

            if ref_id and ref_id == existing_session.leaf_message_id:
                session_to_use = existing_session
                log.info(
                    "Reusing existing chat session (linear).",
                    extra={"session_key": session_key, "message_id": message.id},
                )
            elif ref_id:
                log.info(
                    "Branch detected. Starting new session.",
                    extra={
                        "session_key": session_key,
                        "message_id": message.id,
                        "ref_id": ref_id,
                        "leaf_id": existing_session.leaf_message_id,
                    },
                )
                is_branch = True
                session_key = message.id
            else:
                if session_key == message.id:
                    pass
                else:
                    session_to_use = existing_session

        if session_to_use:
            session_to_use.last_interaction = datetime.utcnow()
            session_to_use.leaf_message_id = message.id
            return session_to_use.chat

        if session_key not in self._session_locks:
            self._session_locks[session_key] = asyncio.Lock()

        async with self._session_locks[session_key]:
            if session_key in self._sessions and not is_branch:
                return self._sessions[session_key].chat

            log.info(
                "Creating new chat session.",
                extra={
                    "session_key": session_key,
                    "message_id": message.id,
                    "is_branch": is_branch,
                },
            )

            history: Any = []
            if is_branch:
                history = await self._reconstruct_history(message)
                log.debug(f"Reconstructed history with {len(history)} turns.")

            tool_declarations = self._tool_registry.get_all_function_declarations()
            system_instruction = self._prompt_builder.system_prompt

            config = self._config_manager.create_config(
                system_instruction_str=system_instruction,
                tool_declarations=tool_declarations,
            )

            chat = self._gemini_core.client.chats.create(
                model=self._settings.MODEL_ID, config=config, history=history
            )

            new_session = ChatSession(
                chat=chat, root_message_id=session_key, leaf_message_id=message.id
            )
            self._sessions[session_key] = new_session

            if session_key in self._session_locks:
                del self._session_locks[session_key]

            return new_session.chat

    async def update_leaf_for_message(self, user_message_id: int, bot_message_id: int):
        """
        Updates the leaf ID for the session associated with the given user message.
        """
        target_session = None
        for _, session in self._sessions.items():
            if session.leaf_message_id == user_message_id:
                target_session = session
                break

        if target_session:
            target_session.leaf_message_id = bot_message_id
            log.debug(
                f"Updated session leaf to {bot_message_id} for user message {user_message_id}"
            )
        else:
            log.warning(
                f"Could not find session to update leaf for user message {user_message_id}"
            )
