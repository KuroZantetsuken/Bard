import logging
from abc import ABC, abstractmethod
from typing import Any, Dict, List, Optional, Protocol, runtime_checkable

import discord
from google.genai import types

log = logging.getLogger("Bard")


@runtime_checkable
class GeminiCoreProtocol(Protocol):
    """
    Defines the required interface for a Gemini core service used by tools.
    This ensures that tools interact with the Gemini API consistently.
    """

    @abstractmethod
    async def generate_content(self, model: str, contents: Any, **kwargs: Any) -> Any:
        """Generates content using the Gemini API."""
        ...

    @abstractmethod
    async def upload_media_bytes(
        self, data_bytes: bytes, display_name: str, mime_type: str
    ) -> Any:
        """Uploads raw media bytes to the Gemini File API."""
        ...


@runtime_checkable
class AttachmentProcessorProtocol(Protocol):
    """
    Defines the required interface for an attachment processing service used by tools.
    This ensures consistent processing of various attachment types.
    """

    @abstractmethod
    async def upload_media_bytes(
        self,
        data_bytes: bytes,
        display_name: str,
        mime_type: str,
        original_url: Optional[str] = None,
    ) -> types.Part:
        """Uploads raw media bytes to the Gemini File API."""
        ...


@runtime_checkable
class ImageScraperProtocol(Protocol):
    """
    Defines the required interface for an image scraping service.
    """

    @abstractmethod
    async def scrape_image_data(self, search_terms: str) -> Optional[bytes]:
        """Scrapes an image and returns its data."""
        ...


class ToolContext:
    """
    A container to pass shared resources and context-specific data to tools.
    It includes explicit service contracts for required dependencies.
    """

    def __init__(
        self,
        settings: Any,
        gemini_core: GeminiCoreProtocol,
        attachment_processor: AttachmentProcessorProtocol,
        image_scraper: ImageScraperProtocol,
        guild: Optional[discord.Guild] = None,
        user_id: Optional[str] = None,
    ):
        """
        Initializes the ToolContext.

        Args:
            settings: Application configuration settings.
            gemini_core: An object implementing GeminiCoreProtocol.
            attachment_processor: An object implementing AttachmentProcessorProtocol.
            image_scraper: An object implementing ImageScraperProtocol.
            guild: Optional; the Discord guild object.
            user_id: Optional; the Discord user ID.
        """
        log.debug(
            "Initializing ToolContext",
            extra={"guild_id": guild.id if guild else None, "user_id": user_id},
        )
        self.settings = settings
        self._validate_service(gemini_core, GeminiCoreProtocol)
        self._validate_service(attachment_processor, AttachmentProcessorProtocol)
        self._validate_service(image_scraper, ImageScraperProtocol)
        self.gemini_core = gemini_core
        self.attachment_processor = attachment_processor
        self.image_scraper = image_scraper

        self.image_data: Optional[Any] = None
        self.image_filename: Optional[str] = None
        self.is_final_output: Optional[bool] = None
        self.tool_response_data: Dict[str, Any] = {}
        self.grounding_sources_md: Optional[str] = None
        self.guild = guild
        self.user_id = user_id

    def _validate_service(self, service: Any, protocol: type) -> None:
        """
        Validates that a service implements the required protocol.

        Args:
            service: The service instance to validate.
            protocol: The protocol (runtime_checkable) that the service must implement.

        Raises:
            TypeError: If the service does not implement the required protocol.
        """
        if not isinstance(service, protocol):
            raise TypeError(
                f"Service {service.__class__.__name__} does not implement "
                f"required protocol {protocol.__name__}"
            )

    def get(self, key: str, default: Optional[Any] = None) -> Optional[Any]:
        """
        Retrieves a value from the tool context by key.

        Args:
            key: The key of the value to retrieve.
            default: The default value to return if the key is not found.
        Returns:
            The value associated with the key, or the default value.
        """
        return self.__dict__.get(key, default)


class BaseTool(ABC):
    """
    Abstract base class for all tools the Gemini bot can use.
    All concrete tools must inherit from this class and implement its abstract methods.
    """

    tool_emoji: Optional[str] = None

    def __init__(self, context: ToolContext):
        """
        Initializes the BaseTool with a ToolContext.

        Args:
            context: The ToolContext object providing shared resources and data.
        """
        log.debug(f"Initializing {self.__class__.__name__}")
        self.context = context

    def function_response_success(
        self, function_name: str, message: str, **kwargs: Any
    ) -> types.FunctionResponse:
        """
        Creates a successful FunctionResponse object.
        """
        return types.FunctionResponse(
            name=function_name,
            response={"status": "success", "message": message, **kwargs},
        )

    def function_response_error(
        self, function_name: str, error_message: str
    ) -> types.FunctionResponse:
        """
        Creates a failed FunctionResponse object.
        """
        return types.FunctionResponse(
            name=function_name, response={"status": "error", "message": error_message}
        )

    @abstractmethod
    def get_function_declarations(self) -> List[types.FunctionDeclaration]:
        """
        Returns a list of Gemini FunctionDeclaration objects that this tool provides.
        These declarations inform the LLM about the tool's capabilities, including
        function names, descriptions, and parameter schemas.
        """
        pass

    @abstractmethod
    async def execute_tool(
        self, function_name: str, args: Dict[str, Any], context: ToolContext
    ) -> types.Part:
        """
        Executes the specified function of the tool.

        Args:
            function_name: The name of the function to execute (must match one from get_function_declarations).
            args: A dictionary of arguments for the function.
            context: A ToolContext object containing shared resources and data for execution.

        Returns:
            A google.genai.types.Part object, typically a FunctionResponse, containing the result of the execution.
        """
        pass
