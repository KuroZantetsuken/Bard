import aiohttp
import asyncio
import discord
import io
import json
import logging
from collections import defaultdict
from config import Config
from datetime import datetime
from google.genai import client
from google.genai import errors as genai_errors
from google.genai import types
from typing import Any
from typing import Dict
from typing import List
from typing import Optional
from typing import Tuple
logger = logging.getLogger("Bard")
def sanitize_response_for_logging(data: Any) -> Any:
    """
    Recursively sanitizes an API response payload for logging.
    - Converts datetime objects to ISO 8601 strings.
    - Removes keys with None values.
    - Replaces raw bytes with a placeholder string.
    """
    if data is None:
        return None
    if isinstance(data, datetime):
        return data.isoformat()
    if isinstance(data, bytes):
        return f"<... {len(data)} bytes of binary data ...>"
    if isinstance(data, dict):
        cleaned_dict = {
            key: sanitized_value
            for key, value in data.items()
            if (sanitized_value := sanitize_response_for_logging(value)) is not None
        }
        return cleaned_dict if cleaned_dict else None
    if isinstance(data, list):
        cleaned_list = [
            sanitized_item
            for item in data
            if (sanitized_item := sanitize_response_for_logging(item)) is not None
        ]
        return cleaned_list if cleaned_list else None
    return data
async def upload_media_bytes_to_file_api(
    gemini_client: client.Client,
    data_bytes: bytes,
    display_name: str,
    mime_type: str
) -> Optional[types.Part]:
    """
    Uploads raw media bytes to the Gemini File API, waits for it to become ACTIVE,
    and returns a Gemini Part.
    """
    if not gemini_client:
        logger.error("❌ Gemini client not initialized. Cannot upload media bytes to File API.")
        return types.Part(text=f"[Attachment: {display_name} - Error: Gemini client not ready]")
    try:
        file_io = io.BytesIO(data_bytes)
        safe_display_name = "".join(c if c.isalnum() or c in ['.', '-', '_'] else '_' for c in display_name)
        if not safe_display_name: safe_display_name = "uploaded_file"
        upload_config = types.UploadFileConfig(
            mime_type=mime_type,
            display_name=safe_display_name,
        )
        logger.info(f"REQUEST to Gemini File API (upload):\n"
                    f"Config:\n{json.dumps(upload_config.dict(), indent=2)}\n"
                    f"Body: Raw file data (name: {display_name}, size: {len(data_bytes)} bytes)")
        uploaded_file = await gemini_client.aio.files.upload(
            file=file_io,
            config=upload_config
        )
        sanitized_response = sanitize_response_for_logging(uploaded_file.dict())
        logger.info(f"RESPONSE from Gemini File API (upload):\n"
                    f"{json.dumps(sanitized_response, indent=2)}")
        if uploaded_file.state.name == "PROCESSING":
            logger.info(f"📎 File '{uploaded_file.name}' is PROCESSING. Polling until ACTIVE...")
            polling_start_time = asyncio.get_event_loop().time()
            max_polling_time = 120
            while uploaded_file.state.name == "PROCESSING":
                if asyncio.get_event_loop().time() - polling_start_time > max_polling_time:
                    logger.error(f"❌ File '{uploaded_file.name}' polling timed out after {max_polling_time}s.")
                    raise TimeoutError(f"File processing timed out for {uploaded_file.name}")
                await asyncio.sleep(2)
                uploaded_file = await gemini_client.aio.files.get(name=uploaded_file.name)
                logger.info(f"📎 Polling status for '{uploaded_file.name}': {uploaded_file.state.name}")
        if uploaded_file.state.name != "ACTIVE":
            error_msg = f"File '{uploaded_file.name}' did not become ACTIVE. Final state: {uploaded_file.state.name}."
            logger.error(f"❌ {error_msg}")
            return types.Part(text=f"[Attachment: {display_name} - Error: {error_msg}]")
        logger.info(f"✅ File '{uploaded_file.name}' is ACTIVE and ready for use.")
        new_file_data = types.FileData(
            mime_type=uploaded_file.mime_type,
            file_uri=uploaded_file.uri
        )
        return types.Part(file_data=new_file_data)
    except Exception as e:
        logger.error(f"❌ Error uploading or processing file '{display_name}' (MIME: {mime_type}) in File API: {e}", exc_info=True)
        return types.Part(text=f"[Attachment: {display_name} - Error: Gemini File API operation failed: {str(e)[:100]}]")
class GeminiConfigManager:
    """Manages the generation configuration for Gemini API calls."""
    @staticmethod
    def get_base_safety_settings() -> List[types.SafetySetting]:
        """Returns a list of base safety settings to disable all harm categories."""
        return [
            types.SafetySetting(category=cat, threshold=types.HarmBlockThreshold.BLOCK_NONE)
            for cat in [
                types.HarmCategory.HARM_CATEGORY_HATE_SPEECH,
                types.HarmCategory.HARM_CATEGORY_DANGEROUS_CONTENT,
                types.HarmCategory.HARM_CATEGORY_SEXUALLY_EXPLICIT,
                types.HarmCategory.HARM_CATEGORY_HARASSMENT
            ]
        ]
    @staticmethod
    def create_main_config(
        system_instruction_str: str,
        tool_declarations: Optional[List[types.FunctionDeclaration]] = None
    ) -> types.GenerateContentConfig:
        """
        Creates the Gemini generation configuration for the main chat interaction.
        """
        tools_list = []
        if tool_declarations:
            custom_functions_tool = types.Tool(function_declarations=tool_declarations)
            tools_list.append(custom_functions_tool)
        safety_settings = GeminiConfigManager.get_base_safety_settings()
        config = types.GenerateContentConfig(
            system_instruction=system_instruction_str if system_instruction_str else None,
            temperature=1.0,
            top_p=0.95,
            max_output_tokens=Config.MAX_OUTPUT_TOKENS,
            safety_settings=safety_settings,
            tools=tools_list if tools_list else None,
        )
        try:
            config.thinking_config = types.ThinkingConfig(
                 include_thoughts=False,
                 thinking_budget=Config.THINKING_BUDGET
            )
        except AttributeError:
            logger.warning("⚠️ Gemini SDK version might not support 'thinking_config'. Proceeding without it.")
        return config
    @staticmethod
    def create_follow_up_config(
        tool_declarations: Optional[List[types.FunctionDeclaration]] = None
    ) -> types.GenerateContentConfig:
        """
        Creates a lightweight config for follow-up calls within a tool-use chain.
        Crucially, it omits the system_instruction to reduce payload size.
        """
        tools_list = []
        if tool_declarations:
            custom_functions_tool = types.Tool(function_declarations=tool_declarations)
            tools_list.append(custom_functions_tool)
        safety_settings = GeminiConfigManager.get_base_safety_settings()
        config = types.GenerateContentConfig(
            temperature=1.0,
            top_p=0.95,
            max_output_tokens=Config.MAX_OUTPUT_TOKENS,
            safety_settings=safety_settings,
            tools=tools_list if tools_list else None,
        )
        try:
            config.thinking_config = types.ThinkingConfig(
                 include_thoughts=False,
                 thinking_budget=Config.THINKING_BUDGET
            )
        except AttributeError:
            logger.warning("⚠️ Gemini SDK version might not support 'thinking_config'. Proceeding without it.")
        return config
class ResponseExtractor:
    @staticmethod
    def extract_text(response: Any) -> str:
        """Attempts to extract textual content from a Gemini API response or Content object."""
        if hasattr(response, 'text') and isinstance(response.text, str):
            return response.text.strip()
        if hasattr(response, 'candidates') and response.candidates:
            content_obj = response.candidates[0].content
        elif hasattr(response, 'parts') and hasattr(response, 'role'):
            content_obj = response
        else:
            content_obj = None
        if content_obj and hasattr(content_obj, 'parts'):
            texts = []
            for part in content_obj.parts:
                if hasattr(part, 'text') and part.text:
                    texts.append(part.text)
            if texts:
                return '\n'.join(texts).strip()
            if any(part.function_call for part in content_obj.parts):
                return ""
        logger.warning(f"⚠️ Failed to extract text from Gemini response of type {type(response)}. Response snippet: {str(response)[:200]}")
        return ""
class AttachmentProcessor:
    """Downloads Discord attachments and prepares them for Gemini using File API."""
    _gemini_file_cache: Dict[int, types.FileData] = {}
    _attachment_upload_locks: defaultdict[int, asyncio.Lock] = defaultdict(asyncio.Lock)
    @staticmethod
    async def _download_attachment_data(
        attachment: discord.Attachment,
        mime_detector_cls
    ) -> Optional[Tuple[bytes, str, str]]:
        """Downloads attachment, detects MIME, returns (bytes, mime_type, filename)."""
        try:
            async with aiohttp.ClientSession() as session:
                async with session.get(attachment.url) as response:
                    if response.status == 200:
                        data = await response.read()
                        mime_type = attachment.content_type
                        if not mime_type or mime_type == 'application/octet-stream' or mime_type == 'unknown/unknown':
                            detected_mime = mime_detector_cls.detect(data)
                            mime_type = detected_mime
                        return data, mime_type, attachment.filename
                    else:
                        logger.warning(f"⚠️ Failed to download attachment {attachment.filename} from {attachment.url}. HTTP Status: {response.status}")
                        return None
        except Exception as e:
            logger.error(f"❌ Error downloading attachment {attachment.filename}: {e}", exc_info=True)
            return None
    @staticmethod
    async def _upload_to_file_api(
        gemini_client: client.Client,
        attachment: discord.Attachment,
        mime_detector_cls
    ) -> Optional[types.Part]:
        """Uploads a single Discord attachment to Gemini File API, using a cache."""
        if not gemini_client:
            logger.error("❌ Gemini client not initialized. Cannot process attachment for File API.")
            return types.Part(text=f"[Attachment: {attachment.filename} - Error: Gemini client not ready]")
        if attachment.id in AttachmentProcessor._gemini_file_cache:
            cached_file_data = AttachmentProcessor._gemini_file_cache[attachment.id]
            logger.info(f"📎 Cache HIT for attachment ID {attachment.id}. Using URI: {cached_file_data.file_uri}")
            return types.Part(file_data=cached_file_data)
        lock = AttachmentProcessor._attachment_upload_locks[attachment.id]
        async with lock:
            if attachment.id in AttachmentProcessor._gemini_file_cache:
                cached_file_data_in_lock = AttachmentProcessor._gemini_file_cache[attachment.id]
                logger.info(f"📎 Cache HIT (after lock) for attachment ID {attachment.id}. Using URI: {cached_file_data_in_lock.file_uri}")
                return types.Part(file_data=cached_file_data_in_lock)
            logger.info(f"📎 Cache MISS for attachment ID {attachment.id}. Downloading '{attachment.filename}'.")
            prepared_data = await AttachmentProcessor._download_attachment_data(attachment, mime_detector_cls)
            if not prepared_data:
                return types.Part(text=f"[Attachment: {attachment.filename} - Error: Download or preparation failed]")
            data_bytes, mime, fname = prepared_data
            uploaded_part = await upload_media_bytes_to_file_api(gemini_client, data_bytes, fname, mime)
            if uploaded_part and uploaded_part.file_data:
                AttachmentProcessor._gemini_file_cache[attachment.id] = uploaded_part.file_data
                logger.info(f"📎 Cached Gemini File API data for attachment ID {attachment.id}.")
            return uploaded_part
    @staticmethod
    async def process_discord_attachments(
        gemini_client: client.Client,
        attachments: List[discord.Attachment],
        mime_detector_cls
    ) -> List[types.Part]:
        """Processes a list of Discord attachments, uploading them and returning Gemini Parts."""
        if not attachments:
            return []
        gemini_parts: List[types.Part] = []
        upload_tasks = []
        for att_obj in attachments:
            upload_tasks.append(AttachmentProcessor._upload_to_file_api(gemini_client, att_obj, mime_detector_cls))
        results = await asyncio.gather(*upload_tasks, return_exceptions=True)
        for result in results:
            if isinstance(result, Exception):
                logger.error(f"❌ Attachment processing task failed: {result}", exc_info=result)
                gemini_parts.append(types.Part(text="[Attachment: Processing failed for one file]"))
            elif result is not None:
                gemini_parts.append(result)
        return gemini_parts