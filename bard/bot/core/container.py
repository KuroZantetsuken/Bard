import logging
from typing import Any, Callable, Dict

from bard.ai.chat.conversation import AIConversation
from bard.ai.chat.history import ChatHistoryManager
from bard.ai.chat.responses import ResponseExtractor
from bard.ai.chat.titler import ThreadTitler
from bard.ai.config.prompts import PromptBuilder, load_prompts_from_directory
from bard.ai.config.settings import GeminiConfigManager
from bard.ai.core import GeminiCore
from bard.ai.files import AttachmentProcessor
from bard.bot.core.coordinator import Coordinator
from bard.bot.lifecycle.events import DiscordEventHandler
from bard.bot.message.parser import MessageParser
from bard.bot.message.sender import MessageSender
from bard.tools.registry import ToolRegistry
from bard.util.media.ffmpeg import FFmpegWrapper
from bard.util.media.media import MimeDetector
from bard.util.system.lifecycle import TaskLifecycleManager
from config import Config

logger = logging.getLogger("Bard")


class Container:
    """
    A simple dependency injection container for managing application services.
    It handles the creation and provision of various components used throughout the bot,
    ensuring that dependencies are met and services are singletons where appropriate.
    """

    def __init__(self, config: Config):
        """
        Initializes the Container with the application configuration.

        Args:
            config: An instance of the Config class containing application settings.
        """
        self.config = config
        self.services: Dict[str, Any] = {}

        self._service_factories: Dict[str, Callable[[], Any]] = {
            "gemini_client": self._create_gemini_client,
            "chat_history_mgr": self._create_chat_history_manager,
            "mime_detector": lambda: MimeDetector(),
            "ffmpeg_wrapper": lambda: FFmpegWrapper(),
            "attachment_processor": self._create_attachment_processor,
            "response_extractor": lambda: ResponseExtractor(),
            "tool_registry": self._create_tool_registry,
            "prompt_builder": self._create_prompt_builder,
            "message_sender": self._create_message_sender,
            "message_parser": self._create_message_parser,
            "gemini_config_manager": self._create_gemini_config_manager,
            "thread_titler": self._create_thread_titler,
            "ai_conversation": self._create_ai_conversation,
            "task_lifecycle_manager": self._create_task_lifecycle_manager,
            "coordinator": self._create_coordinator,
            "discord_event_handler": self._create_discord_event_handler,
        }

    def get(self, service_name: str) -> Any:
        """
        Retrieves a service instance by name. If the service has not been created yet,
        its factory function is called to create and store it.

        Args:
            service_name: The name of the service to retrieve.

        Returns:
            The instance of the requested service.

        Raises:
            ValueError: If an unknown service name is requested.
        """
        if service_name not in self.services:
            if service_name not in self._service_factories:
                raise ValueError(f"Unknown service: {service_name}")
            logger.debug(f"Creating service: {service_name}")
            self.services[service_name] = self._service_factories[service_name]()
            logger.debug(f"Service created: {service_name}")
        return self.services[service_name]

    def _create_gemini_client(self) -> GeminiCore:
        """Creates and returns an instance of GeminiCore."""
        if not self.config.GEMINI_API_KEY:
            raise ValueError("GEMINI_API_KEY is not set in the configuration.")
        return GeminiCore(api_key=self.config.GEMINI_API_KEY)

    def _create_chat_history_manager(self) -> ChatHistoryManager:
        """Creates and returns an instance of ChatHistoryManager."""
        return ChatHistoryManager(
            max_history_turns=self.config.MAX_HISTORY_TURNS,
            max_history_age=self.config.MAX_HISTORY_AGE,
        )

    def _create_attachment_processor(self) -> AttachmentProcessor:
        """Creates and returns an instance of AttachmentProcessor."""
        return AttachmentProcessor(gemini_core=self.get("gemini_client"))

    def _create_tool_registry(self) -> ToolRegistry:
        """Creates and returns an instance of ToolRegistry."""
        return ToolRegistry(
            config=self.config,
            gemini_client=self.get("gemini_client"),
            response_extractor=self.get("response_extractor"),
            attachment_processor=self.get("attachment_processor"),
            ffmpeg_wrapper=self.get("ffmpeg_wrapper"),
            mime_detector=self.get("mime_detector"),
        )

    def _create_prompt_builder(self) -> PromptBuilder:
        """Creates and returns an instance of PromptBuilder."""
        system_prompt = load_prompts_from_directory(self.config.PROMPT_DIR)

        return PromptBuilder(
            attachment_processor=self.get("attachment_processor"),
            system_prompt=system_prompt,
        )

    def _create_message_sender(self) -> MessageSender:
        """Creates and returns an instance of MessageSender."""
        if not self.config.DISCORD_BOT_TOKEN:
            raise ValueError("DISCORD_BOT_TOKEN is not set in the configuration.")
        return MessageSender(
            bot_token=self.config.DISCORD_BOT_TOKEN,
            retry_emoji=self.config.RETRY_EMOJI,
            cancel_emoji=self.config.CANCEL_EMOJI,
            logger=logger,
            thread_titler=self.get("thread_titler"),
        )

    def _create_message_parser(self) -> MessageParser:
        """Creates and returns an instance of MessageParser."""
        return MessageParser(attachment_processor=self.get("attachment_processor"))

    def _create_gemini_config_manager(self) -> GeminiConfigManager:
        """Creates and returns an instance of GeminiConfigManager."""
        return GeminiConfigManager(
            max_output_tokens=self.config.MAX_OUTPUT_TOKENS,
            thinking_budget=self.config.THINKING_BUDGET,
        )

    def _create_thread_titler(self) -> ThreadTitler:
        """Creates and returns an instance of ThreadTitler."""
        return ThreadTitler(
            gemini_core=self.get("gemini_client"),
            gemini_config_manager=self.get("gemini_config_manager"),
            config=self.config,
        )

    def _create_ai_conversation(self) -> AIConversation:
        """Creates and returns an instance of AIConversation."""
        return AIConversation(
            config=self.config,
            core=self.get("gemini_client"),
            config_manager=self.get("gemini_config_manager"),
            prompt_builder=self.get("prompt_builder"),
            chat_history_manager=self.get("chat_history_mgr"),
            tool_registry=self.get("tool_registry"),
        )

    def _create_task_lifecycle_manager(self) -> TaskLifecycleManager:
        """
        Creates and returns an instance of TaskLifecycleManager.
        Handles circular dependency by temporarily storing the manager.
        """
        manager = TaskLifecycleManager()
        self.services["task_lifecycle_manager"] = manager
        manager.coordinator = self.get("coordinator")
        return manager

    def _create_coordinator(self) -> Coordinator:
        """Creates and returns an instance of Coordinator."""
        return Coordinator(
            message_parser=self.get("message_parser"),
            ai_conversation=self.get("ai_conversation"),
            message_sender=self.get("message_sender"),
            task_lifecycle_manager=self.get("task_lifecycle_manager"),
        )

    def _create_discord_event_handler(self) -> DiscordEventHandler:
        """Creates and returns an instance of DiscordEventHandler."""
        return DiscordEventHandler(
            task_lifecycle_manager=self.get("task_lifecycle_manager"),
            config=self.config,
            bot_user_id=None,
        )
