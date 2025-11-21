import logging
from typing import Any, Callable, Dict

from ai.chat.conversation import AIConversation
from ai.chat.files import AttachmentProcessor
from ai.chat.sessions import ChatSessionManager
from ai.chat.titler import ThreadTitler
from ai.config import GeminiConfigManager
from ai.context.prompts import PromptBuilder, load_prompts_from_directory
from ai.core import GeminiCore
from ai.tools.registry import ToolRegistry
from bot.core.coordinator import Coordinator
from bot.core.events import DiscordEventHandler
from bot.core.lifecycle import RequestManager
from bot.core.typing import TypingManager
from bot.message.parser import MessageParser
from bot.message.reactions import ReactionManager
from bot.message.sender import MessageSender
from scraper.cache import CacheManager
from scraper.image import ImageScraper
from scraper.orchestrator import ScrapingOrchestrator
from scraper.scraper import Scraper
from scraper.video import VideoHandler
from settings import Settings

log = logging.getLogger("Bard")


class Container:
    """
    A simple dependency injection container for managing application services.
    It handles the creation and provision of various components used throughout the bot,
    ensuring that dependencies are met and services are singletons where appropriate.
    """

    def __init__(self, settings: Settings):
        """
        Initializes the Container with the application configuration.

        Args:
            settings: An instance of the Config class containing application settings.
        """
        self.settings = settings
        self.services: Dict[str, Any] = {}
        log.debug("Container initialized.")

        self._service_factories: Dict[str, Callable[[], Any]] = {
            "gemini_core": self._create_gemini_core,
            "video_handler": self._create_video_handler,
            "attachment_processor": self._create_attachment_processor,
            "tool_registry": self._create_tool_registry,
            "prompt_builder": self._create_prompt_builder,
            "message_sender": self._create_message_sender,
            "scraper": self._create_scraper,
            "cache_manager": self._create_cache_manager,
            "scraping_orchestrator": self._create_scraping_orchestrator,
            "message_parser": self._create_message_parser,
            "gemini_config_manager": self._create_gemini_config_manager,
            "thread_titler": self._create_thread_titler,
            "ai_conversation": self._create_ai_conversation,
            "typing_manager": self._create_typing_manager,
            "request_manager": self._create_request_manager,
            "reaction_manager": self._create_reaction_manager,
            "coordinator": self._create_coordinator,
            "discord_event_handler": self._create_discord_event_handler,
            "image_scraper": self._create_image_scraper,
            "chat_session_manager": self._create_chat_session_manager,
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
                log.error(f"Attempted to access unknown service: {service_name}")
                raise ValueError(f"Unknown service: {service_name}")
            log.debug(f"Creating service: {service_name}")
            self.services[service_name] = self._service_factories[service_name]()
            log.debug(f"Service created: {service_name}")
        else:
            log.debug(f"Returning existing service: {service_name}")
        return self.services[service_name]

    def _create_gemini_core(self) -> GeminiCore:
        """Creates and returns an instance of GeminiCore."""
        if not self.settings.GEMINI_API_KEY:
            log.error("GEMINI_API_KEY is not set. Service creation failed.")
            raise ValueError("GEMINI_API_KEY is not set in the configuration.")
        log.debug("GeminiCore instance created.")
        return GeminiCore(
            api_key=self.settings.GEMINI_API_KEY, base_url=self.settings.GEMINI_BASE_URL
        )

    def _create_attachment_processor(self) -> AttachmentProcessor:
        """Creates and returns an instance of AttachmentProcessor."""
        log.debug("AttachmentProcessor instance created.")
        return AttachmentProcessor(gemini_core=self.get("gemini_core"))

    def _create_tool_registry(self) -> ToolRegistry:
        """Creates and returns an instance of ToolRegistry."""
        log.debug("ToolRegistry instance created.")
        return ToolRegistry(
            settings=self.settings,
            gemini_core=self.get("gemini_core"),
            attachment_processor=self.get("attachment_processor"),
            image_scraper=self.get("image_scraper"),
        )

    def _create_prompt_builder(self) -> PromptBuilder:
        """Creates and returns an instance of PromptBuilder."""
        system_prompt = load_prompts_from_directory(self.settings.PROMPT_DIR)
        log.debug("Prompts loaded from directory.", extra={"count": len(system_prompt)})
        return PromptBuilder(
            attachment_processor=self.get("attachment_processor"),
            system_prompt=system_prompt,
        )

    def _create_message_sender(self) -> MessageSender:
        """Creates and returns an instance of MessageSender."""
        if not self.settings.DISCORD_BOT_TOKEN:
            log.error("DISCORD_BOT_TOKEN is not set. Service creation failed.")
            raise ValueError("DISCORD_BOT_TOKEN is not set in the configuration.")
        log.debug("MessageSender instance created.")
        return MessageSender(
            bot_token=self.settings.DISCORD_BOT_TOKEN,
            retry_emoji=self.settings.RETRY_EMOJI,
            cancel_emoji=self.settings.CANCEL_EMOJI,
            thread_titler=self.get("thread_titler"),
        )

    def _create_scraper(self) -> Scraper:
        """Creates and returns an instance of Scraper."""
        log.debug("Scraper instance created.")
        return Scraper()

    def _create_cache_manager(self) -> CacheManager:
        """Creates and returns an instance of CacheManager."""
        log.debug("CacheManager instance created.")
        return CacheManager()

    def _create_video_handler(self) -> VideoHandler:
        """Creates and returns an instance of VideoHandler."""
        log.debug("VideoHandler instance created.")
        return VideoHandler(cache_manager=self.get("cache_manager"))

    def _create_scraping_orchestrator(self) -> ScrapingOrchestrator:
        """Creates and returns an instance of ScrapingOrchestrator."""
        log.debug("ScrapingOrchestrator instance created.")
        return ScrapingOrchestrator(
            cache_manager=self.get("cache_manager"),
            scraper=self.get("scraper"),
            video_handler=self.get("video_handler"),
            image_scraper=self.get("image_scraper"),
        )

    def _create_message_parser(self) -> MessageParser:
        """Creates and returns an instance of MessageParser."""
        log.debug("MessageParser instance created.")
        return MessageParser(
            attachment_processor=self.get("attachment_processor"),
            scraping_orchestrator=self.get("scraping_orchestrator"),
        )

    def _create_gemini_config_manager(self) -> GeminiConfigManager:
        """Creates and returns an instance of GeminiConfigManager."""
        log.debug("GeminiConfigManager instance created.")
        return GeminiConfigManager(
            max_output_tokens=self.settings.MAX_OUTPUT_TOKENS,
            thinking_budget=self.settings.THINKING_BUDGET,
        )

    def _create_thread_titler(self) -> ThreadTitler:
        """Creates and returns an instance of ThreadTitler."""
        log.debug("ThreadTitler instance created.")
        return ThreadTitler(
            gemini_core=self.get("gemini_core"),
            gemini_config_manager=self.get("gemini_config_manager"),
            settings=self.settings,
        )

    def _create_ai_conversation(self) -> AIConversation:
        """Creates and returns an instance of AIConversation."""
        log.debug("AIConversation instance created.")
        return AIConversation(
            settings=self.settings,
            core=self.get("gemini_core"),
            config_manager=self.get("gemini_config_manager"),
            prompt_builder=self.get("prompt_builder"),
            tool_registry=self.get("tool_registry"),
            scraping_orchestrator=self.get("scraping_orchestrator"),
        )

    def _create_typing_manager(self) -> TypingManager:
        """Creates and returns an instance of TypingManager."""
        log.debug("TypingManager instance created.")
        return TypingManager()

    def _create_request_manager(self) -> RequestManager:
        """Creates and returns an instance of RequestManager."""
        log.debug("RequestManager instance created.")
        return RequestManager(
            reaction_manager=self.get("reaction_manager"),
            typing_manager=self.get("typing_manager"),
        )

    def _create_reaction_manager(self) -> ReactionManager:
        """Creates and returns an instance of ReactionManager."""
        log.debug("ReactionManager instance created.")
        return ReactionManager(
            retry_emoji=self.settings.RETRY_EMOJI,
            cancel_emoji=self.settings.CANCEL_EMOJI,
        )

    def _create_coordinator(self) -> Coordinator:
        """Creates and returns an instance of Coordinator."""
        log.debug("Coordinator instance created.")
        return Coordinator(
            message_parser=self.get("message_parser"),
            ai_conversation=self.get("ai_conversation"),
            message_sender=self.get("message_sender"),
            request_manager=self.get("request_manager"),
            reaction_manager=self.get("reaction_manager"),
            scraping_orchestrator=self.get("scraping_orchestrator"),
            typing_manager=self.get("typing_manager"),
            chat_session_manager=self.get("chat_session_manager"),
        )

    def _create_discord_event_handler(self) -> DiscordEventHandler:
        """Creates and returns an instance of DiscordEventHandler."""
        log.debug("DiscordEventHandler instance created.")
        return DiscordEventHandler(
            request_manager=self.get("request_manager"),
            coordinator=self.get("coordinator"),
            reaction_manager=self.get("reaction_manager"),
            typing_manager=self.get("typing_manager"),
            settings=self.settings,
            bot_user_id=None,
        )

    def _create_image_scraper(self) -> ImageScraper:
        """Creates and returns an instance of ImageScraper."""
        log.debug("ImageScraper instance created.")
        return ImageScraper(scraper=self.get("scraper"))

    def _create_chat_session_manager(self) -> ChatSessionManager:
        """Creates and returns an instance of ChatSessionManager."""
        log.debug("ChatSessionManager instance created.")
        return ChatSessionManager(
            settings=self.settings,
            gemini_core=self.get("gemini_core"),
            prompt_builder=self.get("prompt_builder"),
            config_manager=self.get("gemini_config_manager"),
            tool_registry=self.get("tool_registry"),
        )
