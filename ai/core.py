import asyncio
import io
import logging
from typing import Any, List

from google.genai import client as genai_client
from google.genai import errors as genai_errors
from google.genai import types as gemini_types

# Initialize logger for the Gemini core module.
logger = logging.getLogger("Bard")


class GeminiCore:
    """
    Core Gemini API wrapper responsible for initializing the Gemini client,
    generating content (both standard and streaming), and handling file uploads.
    """

    def __init__(self, api_key: str):
        """
        Initializes the GeminiCore with the provided API key.

        Args:
            api_key: The API key for authenticating with the Gemini API.
        """
        self.api_key = api_key
        self.client: genai_client.Client = genai_client.Client(api_key=self.api_key)

    @property
    def aio(self) -> Any:
        """
        Exposes the asynchronous client for direct access by tools or other modules
        that require direct interaction with the Gemini API client.
        """
        return self.client.aio

    async def generate_content(
        self, model: str, contents: List[gemini_types.Content], **kwargs: Any
    ) -> Any:
        """
        Generates content using the Gemini API's asynchronous method. Supports streaming
        if 'stream=True' is passed in kwargs.

        Args:
            model: The name of the Gemini model to use (e.g., "gemini-pro").
            contents: A list of content parts to send to the model.
            **kwargs: Additional keyword arguments to pass to the API call, including 'stream'.

        Returns:
            The response object from the Gemini API (or an asynchronous iterator if streaming).

        Raises:
            genai_errors.APIError: If an API-related error occurs during content generation.
        """
        try:
            response = await self.client.aio.models.generate_content(
                model=model, contents=contents, **kwargs
            )
            return response
        except genai_errors.APIError as e:
            logger.error(
                f"Gemini API error during content generation for model '{model}': {e}",
                exc_info=True,
            )
            raise

    async def upload_media_bytes(
        self, data_bytes: bytes, display_name: str, mime_type: str
    ) -> Any:
        """
        Uploads raw media bytes to the Gemini File API and returns a Gemini Part object
        referencing the uploaded file. Handles file processing status.

        Args:
            data_bytes: The raw bytes of the media file.
            display_name: A human-readable name for the file.
            mime_type: The MIME type of the media file (e.g., "image/png", "video/mp4").

        Returns:
            A gemini_types.Part object containing file_data with the URI of the uploaded file.
            Returns a text part with an error message if the upload fails.
        """
        try:
            file_io = io.BytesIO(data_bytes)
            # Sanitize display name for file upload.
            safe_display_name = (
                "".join(
                    c if c.isalnum() or c in [".", "-", "_"] else "_"
                    for c in display_name
                )
                or "uploaded_file"
            )
            upload_config = gemini_types.UploadFileConfig(
                mime_type=mime_type,
                display_name=safe_display_name,
            )
            uploaded_file_result = await self.client.aio.files.upload(
                file=file_io, config=upload_config
            )

            if not uploaded_file_result or not uploaded_file_result.name:
                raise ValueError("File upload failed to return a valid result.")

            active_uploaded_file = uploaded_file_result
            # Poll for file processing status until it becomes ACTIVE or times out.
            if (
                active_uploaded_file.state
                and active_uploaded_file.state.name == "PROCESSING"
            ):
                logger.info("File is PROCESSING. Polling until ACTIVE.")
                polling_start_time = asyncio.get_event_loop().time()
                while (
                    active_uploaded_file.state
                    and active_uploaded_file.state.name == "PROCESSING"
                ):
                    if (
                        asyncio.get_event_loop().time() - polling_start_time > 120
                    ):  # 2 minutes timeout
                        raise TimeoutError("File processing timed out.")
                    await asyncio.sleep(2)
                    if active_uploaded_file.name:
                        active_uploaded_file = await self.client.aio.files.get(
                            name=active_uploaded_file.name
                        )

            if (
                not active_uploaded_file.state
                or active_uploaded_file.state.name != "ACTIVE"
            ):
                final_state = (
                    active_uploaded_file.state.name
                    if active_uploaded_file.state
                    else "UNKNOWN"
                )
                raise ValueError(
                    f"File did not become ACTIVE. Final state: {final_state}"
                )

            return gemini_types.Part(
                file_data=gemini_types.FileData(
                    mime_type=active_uploaded_file.mime_type,
                    file_uri=active_uploaded_file.uri,
                )
            )
        except Exception as e:
            logger.error(f"Error uploading file '{display_name}': {e}", exc_info=True)
            return gemini_types.Part(
                text=f"[Attachment: {display_name} - Error: {str(e)[:100]}]"
            )
