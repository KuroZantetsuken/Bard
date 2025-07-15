import logging
from abc import ABC, abstractmethod
from typing import (
    Any,
    Dict,
    List,
    Optional,
    Protocol,
    Tuple,
    runtime_checkable,
)

import discord
from google.genai import types

# Initialize logger for the base tools module.
logger = logging.getLogger("Bard")


@runtime_checkable
class GeminiClientProtocol(Protocol):
    """
    Defines the required interface for a Gemini client service used by tools.
    This ensures that tools interact with the Gemini API consistently.
    """

    @abstractmethod
    async def generate_content(
        self, model: str, contents: List[Any], **kwargs: Any
    ) -> Any:
        """Generates content using the Gemini API."""
        ...

    @abstractmethod
    async def upload_media_bytes(
        self, data_bytes: bytes, display_name: str, mime_type: str
    ) -> Any:
        """Uploads raw media bytes to the Gemini File API."""
        ...


@runtime_checkable
class MemoryServiceProtocol(Protocol):
    """
    Defines the required interface for a memory service used by tools.
    This ensures consistent interaction with user-specific long-term memories.
    """

    @abstractmethod
    async def get_memory(self, user_id: str, memory_key: str) -> Optional[Any]:
        """Retrieves a specific memory for a user by content key."""
        ...

    @abstractmethod
    async def set_memory(
        self, user_id: str, memory_key: str, memory_value: Any
    ) -> None:
        """Sets or updates a specific memory for a user by content key."""
        ...

    @abstractmethod
    async def add_memory(self, user_id: str, memory_content: str) -> bool:
        """Adds a new memory for a user."""
        ...

    @abstractmethod
    async def remove_memory(self, user_id: str, memory_id: int) -> bool:
        """Removes a specific memory by ID."""
        ...


@runtime_checkable
class ResponseExtractorProtocol(Protocol):
    """
    Defines the required interface for a response extraction service used by tools.
    This ensures consistent extraction of content and metadata from API responses.
    """

    @abstractmethod
    def extract_response(self, response: Any) -> str:
        """Extracts textual content from an API response."""
        ...

    @abstractmethod
    def extract_metadata(self, response: Any) -> Dict[str, Any]:
        """Extracts metadata from an API response."""
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

    @abstractmethod
    async def process_image_url(self, url: str) -> Optional[types.Part]:
        """Downloads an image from a given URL and processes it for Gemini."""
        ...

    @abstractmethod
    def get_original_url(self, gemini_file_uri: str) -> Optional[str]:
        """Retrieves the original public URL associated with a Gemini File API URI."""
        ...


@runtime_checkable
class FFmpegWrapperProtocol(Protocol):
    """
    Defines the required interface for an FFmpeg wrapper service used by tools.
    This ensures consistent execution of FFmpeg commands for media processing.
    """

    @abstractmethod
    async def execute(
        self,
        arguments: List[str],
        input_data: Optional[bytes] = None,
        timeout: float = 30.0,
    ) -> Tuple[Optional[bytes], Optional[bytes], int]:
        """Executes an FFmpeg command."""
        ...

    @abstractmethod
    async def convert_audio(
        self,
        input_data: bytes,
        input_format: str,
        output_format: str,
        input_args: List[str] = [],
        output_args: List[str] = [],
        timeout: float = 30.0,
    ) -> Optional[bytes]:
        """Converts audio data from one format to another using FFmpeg."""
        ...


@runtime_checkable
class MimeDetectorProtocol(Protocol):
    """
    Defines the required interface for a MIME type detection service used by tools.
    This ensures consistent detection and extension retrieval for various media types.
    """

    @abstractmethod
    def detect(self, data: bytes) -> str:
        """Detects the MIME type of given binary data."""
        ...

    @abstractmethod
    def get_extension(self, mime_type: str) -> str:
        """Returns the file extension for a given MIME type."""
        ...


class ToolContext:
    """
    A container to pass shared resources and context-specific data to tools.
    It includes explicit service contracts for required dependencies.
    """

    def __init__(
        self,
        config: Any,
        gemini_client: GeminiClientProtocol,
        memory_service: MemoryServiceProtocol,
        response_extractor: ResponseExtractorProtocol,
        attachment_processor: AttachmentProcessorProtocol,
        ffmpeg_wrapper: FFmpegWrapperProtocol,
        mime_detector: MimeDetectorProtocol,
        full_conversation_for_tooling: Optional[List[Any]] = None,
        guild: Optional[discord.Guild] = None,
        **kwargs: Any,
    ):
        """
        Initializes the ToolContext.

        Args:
            config: Application configuration settings.
            gemini_client: An object implementing GeminiClientProtocol.
            memory_service: An object implementing MemoryServiceProtocol.
            response_extractor: An object implementing ResponseExtractorProtocol.
            attachment_processor: An object implementing AttachmentProcessorProtocol.
            ffmpeg_wrapper: An object implementing FFmpegWrapperProtocol.
            mime_detector: An object implementing MimeDetectorProtocol.
            full_conversation_for_tooling: Optional; the full conversation history for tool context.
            guild: Optional; the Discord guild object.
            **kwargs: Additional keyword arguments for flexible context data.
        """
        self.config = config
        self._validate_service(gemini_client, GeminiClientProtocol)
        self._validate_service(memory_service, MemoryServiceProtocol)
        self._validate_service(response_extractor, ResponseExtractorProtocol)
        self._validate_service(attachment_processor, AttachmentProcessorProtocol)
        self._validate_service(ffmpeg_wrapper, FFmpegWrapperProtocol)
        self._validate_service(mime_detector, MimeDetectorProtocol)
        self.gemini_client = gemini_client
        self.memory_service = memory_service
        self.response_extractor = response_extractor
        self.attachment_processor = attachment_processor
        self.ffmpeg_wrapper = ffmpeg_wrapper
        self.mime_detector = mime_detector
        self.image_data: Optional[Any] = None
        self.image_filename: Optional[str] = None
        self.is_final_output: Optional[bool] = None
        self.tool_response_data: Dict[str, Any] = {}
        self.grounding_sources_md: Optional[str] = None
        self.full_conversation_for_tooling = full_conversation_for_tooling
        self.guild = guild
        self.__dict__.update(kwargs)

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
