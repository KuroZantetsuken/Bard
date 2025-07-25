import logging
from typing import Any, Callable, Dict

from ai.context import ChatHistoryManager
from ai.conversation import AIConversation
from ai.core import GeminiCore
from ai.files import AttachmentProcessor
from ai.memory import MemoryManager
from ai.prompts import PromptBuilder, load_prompts_from_directory
from ai.responses import ResponseExtractor
from ai.settings import GeminiConfigManager
from bot.commands import CommandHandler
from bot.coordinator import Coordinator
from bot.events import DiscordEventHandler
from bot.parser import MessageParser
from bot.router import CommandRouter
from bot.sender import MessageSender
from config import Config
from tools.registry import ToolRegistry
from utilities.ffmpeg import FFmpegWrapper
from utilities.lifecycle import TaskLifecycleManager
from utilities.media import MimeDetector

# Initialize logger for the dependency injection container.
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
        # Defines factories for creating service instances.
        self._service_factories: Dict[str, Callable[[], Any]] = {
            "gemini_client": self._create_gemini_client,
            "chat_history_mgr": self._create_chat_history_manager,
            "memory_manager": self._create_memory_manager,
            "mime_detector": lambda: MimeDetector(),
            "ffmpeg_wrapper": lambda: FFmpegWrapper(),
            "attachment_processor": self._create_attachment_processor,
            "response_extractor": lambda: ResponseExtractor(),
            "tool_registry": self._create_tool_registry,
            "prompt_builder": self._create_prompt_builder,
            "message_sender": self._create_message_sender,
            "command_handler": self._create_command_handler,
            "command_router": lambda: CommandRouter(),
            "message_parser": self._create_message_parser,
            "gemini_config_manager": self._create_gemini_config_manager,
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
            history_dir=self.config.HISTORY_DIR,
            max_history_turns=self.config.MAX_HISTORY_TURNS,
            max_history_age=self.config.MAX_HISTORY_AGE,
        )

    def _create_memory_manager(self) -> MemoryManager:
        """Creates and returns an instance of MemoryManager."""
        return MemoryManager(
            memory_dir=self.config.MEMORY_DIR,
            max_memories=self.config.MAX_MEMORIES,
        )

    def _create_attachment_processor(self) -> AttachmentProcessor:
        """Creates and returns an instance of AttachmentProcessor."""
        return AttachmentProcessor(gemini_core=self.get("gemini_client"))

    def _create_tool_registry(self) -> ToolRegistry:
        """Creates and returns an instance of ToolRegistry."""
        return ToolRegistry(
            config=self.config,
            gemini_client=self.get("gemini_client"),
            memory_service=self.get("memory_manager"),
            response_extractor=self.get("response_extractor"),
            attachment_processor=self.get("attachment_processor"),
            ffmpeg_wrapper=self.get("ffmpeg_wrapper"),
            mime_detector=self.get("mime_detector"),
        )

    def _create_prompt_builder(self) -> PromptBuilder:
        """Creates and returns an instance of PromptBuilder."""
        system_prompt = load_prompts_from_directory(self.config.PROMPT_DIR)

        async def context_manager_wrapper(user_id: int):
            user_id_str = str(user_id)
            memories = await self.get("memory_manager").load_memories(user_id_str)
            formatted_memories = self.get("memory_manager").format_memories(
                user_id_str, memories
            )
            return memories, formatted_memories

        return PromptBuilder(
            context_manager=context_manager_wrapper,
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
            logger=logger,
        )

    def _create_command_handler(self) -> CommandHandler:
        """Creates and returns an instance of CommandHandler."""
        return CommandHandler(
            context_manager=self.get("chat_history_mgr"),
            memory_manager=self.get("memory_manager"),
            message_sender=self.get("message_sender"),
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

    def _create_ai_conversation(self) -> AIConversation:
        """Creates and returns an instance of AIConversation."""
        return AIConversation(
            config=self.config,
            core=self.get("gemini_client"),
            config_manager=self.get("gemini_config_manager"),
            prompt_builder=self.get("prompt_builder"),
            chat_history_manager=self.get("chat_history_mgr"),
            memory_manager=self.get("memory_manager"),
            tool_registry=self.get("tool_registry"),
        )

    def _create_task_lifecycle_manager(self) -> TaskLifecycleManager:
        """
        Creates and returns an instance of TaskLifecycleManager.
        Handles circular dependency by temporarily storing the manager.
        """
        manager = TaskLifecycleManager()
        self.services["task_lifecycle_manager"] = manager  # Temporary store.
        manager.coordinator = self.get(
            "coordinator"
        )  # Set coordinator after it's available.
        return manager

    def _create_coordinator(self) -> Coordinator:
        """Creates and returns an instance of Coordinator."""
        return Coordinator(
            message_parser=self.get("message_parser"),
            ai_conversation=self.get("ai_conversation"),
            message_sender=self.get("message_sender"),
            command_handler=self.get("command_handler"),
            task_lifecycle_manager=self.get("task_lifecycle_manager"),
        )

    def _create_discord_event_handler(self) -> DiscordEventHandler:
        """Creates and returns an instance of DiscordEventHandler."""
        return DiscordEventHandler(
            task_lifecycle_manager=self.get("task_lifecycle_manager"),
            config=self.config,
            bot_user_id=None,
        )
