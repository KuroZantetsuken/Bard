import logging
import io
import discord
import asyncio
import aiohttp
from typing import List, Optional, Dict, Tuple, Any
from google.genai import types, client
from config import Config
from collections import defaultdict
logger = logging.getLogger("Bard")
class GeminiConfigManager:
    """Manages the generation configuration for Gemini API calls."""
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
        safety_settings = [
            types.SafetySetting(category=cat, threshold=types.HarmBlockThreshold.BLOCK_NONE)
            for cat in [
                types.HarmCategory.HARM_CATEGORY_HATE_SPEECH,
                types.HarmCategory.HARM_CATEGORY_DANGEROUS_CONTENT,
                types.HarmCategory.HARM_CATEGORY_SEXUALLY_EXPLICIT,
                types.HarmCategory.HARM_CATEGORY_HARASSMENT
            ]
        ]
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
    def create_code_execution_config() -> types.GenerateContentConfig:
        """
        Creates the Gemini generation configuration for a code execution call.
        """
        safety_settings = [
            types.SafetySetting(category=cat, threshold=types.HarmBlockThreshold.BLOCK_NONE)
            for cat in [
                types.HarmCategory.HARM_CATEGORY_HATE_SPEECH,
                types.HarmCategory.HARM_CATEGORY_DANGEROUS_CONTENT,
                types.HarmCategory.HARM_CATEGORY_SEXUALLY_EXPLICIT,
                types.HarmCategory.HARM_CATEGORY_HARASSMENT
            ]
        ]
        config = types.GenerateContentConfig(
            temperature=0.8,
            top_p=0.95,
            max_output_tokens=Config.MAX_OUTPUT_TOKENS,
            safety_settings=safety_settings,
            tools=[types.Tool(code_execution=types.ToolCodeExecution())],
        )
        return config
    @staticmethod
    def create_tooling_config() -> types.GenerateContentConfig:
        """
        Creates the Gemini generation configuration for the internal tooling call,
        enabling built-in tools like Google Search and URL Context.
        """
        google_search_tool = types.Tool(google_search_retrieval=types.GoogleSearchRetrieval())
        available_tools_for_native = [
            types.Tool(google_search=types.GoogleSearch()),
            types.Tool(url_context=types.UrlContext()),
        ]
        safety_settings = [
            types.SafetySetting(category=cat, threshold=types.HarmBlockThreshold.BLOCK_NONE)
            for cat in [
                types.HarmCategory.HARM_CATEGORY_HATE_SPEECH,
                types.HarmCategory.HARM_CATEGORY_DANGEROUS_CONTENT,
                types.HarmCategory.HARM_CATEGORY_SEXUALLY_EXPLICIT,
                types.HarmCategory.HARM_CATEGORY_HARASSMENT
            ]
        ]
        config = types.GenerateContentConfig(
            system_instruction=types.Content(parts=[types.Part(text="Your critical function is to always search the internet or analyze URLs for extra information.")], role="system"),
            temperature=1.0,
            top_p=0.95,
            max_output_tokens=Config.MAX_OUTPUT_TOKENS,
            safety_settings=safety_settings,
            tools=available_tools_for_native,
        )
        try:
            config.thinking_config = types.ThinkingConfig(
                 include_thoughts=False,
                 thinking_budget=Config.THINKING_BUDGET
            )
        except AttributeError:
            logger.warning("⚠️ Gemini SDK version might not support 'thinking_config' for tooling. Proceeding without it.")
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
    ) -> Optional[Tuple[io.BytesIO, str, str]]:
        """Downloads attachment, detects MIME, returns (BytesIO, mime_type, filename)."""
        try:
            async with aiohttp.ClientSession() as session:
                async with session.get(attachment.url) as response:
                    if response.status == 200:
                        data = await response.read()
                        mime_type = attachment.content_type
                        if not mime_type or mime_type == 'application/octet-stream' or mime_type == 'unknown/unknown':
                            detected_mime = mime_detector_cls.detect(data)
                            mime_type = detected_mime
                        return io.BytesIO(data), mime_type, attachment.filename
                    else:
                        logger.warning(f"⚠️ Failed to download attachment {attachment.filename} from {attachment.url}. HTTP Status: {response.status}")
                        return None
        except Exception as e:
            logger.error(f"❌ Error downloading attachment {attachment.filename}: {e}", exc_info=True)
            return None
    @staticmethod
    async def _upload_to_file_api(
        gemini_client_instance: client.Client,
        attachment: discord.Attachment,
        mime_detector_cls
    ) -> Optional[types.Part]:
        """Uploads a single Discord attachment to Gemini File API, using a cache."""
        if not gemini_client_instance:
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
            file_io, mime, fname = prepared_data
            try:
                file_io.seek(0)
                display_name = "".join(c if c.isalnum() or c in ['.', '-', '_'] else '_' for c in fname)
                if not display_name: display_name = "uploaded_file"
                uploaded_gemini_file_obj = await gemini_client_instance.aio.files.upload(
                    file=file_io,
                    config=types.UploadFileConfig(
                        mime_type=mime,
                        display_name=display_name,
                    )
                )
                logger.info(f"📎 Successfully uploaded '{fname}' to Gemini File API. URI: {uploaded_gemini_file_obj.uri}, Gemini Name: {uploaded_gemini_file_obj.name}")
                new_file_data_to_cache = types.FileData(
                    mime_type=uploaded_gemini_file_obj.mime_type,
                    file_uri=uploaded_gemini_file_obj.uri
                )
                AttachmentProcessor._gemini_file_cache[attachment.id] = new_file_data_to_cache
                logger.info(f"📎 Cached Gemini File API data for attachment ID {attachment.id}.")
                return types.Part(file_data=new_file_data_to_cache)
            except Exception as e:
                logger.error(f"❌ Error uploading file '{fname}' (MIME: {mime}) to Gemini File API: {e}", exc_info=True)
                return types.Part(text=f"[Attachment: {fname} - Error: Gemini File API Upload failed: {str(e)[:100]}]")
    @staticmethod
    async def process_discord_attachments(
        gemini_client_instance: client.Client,
        attachments: List[discord.Attachment],
        mime_detector_cls
    ) -> List[types.Part]:
        """Processes a list of Discord attachments, uploading them and returning Gemini Parts."""
        if not attachments:
            return []
        gemini_parts: List[types.Part] = []
        upload_tasks = []
        for att_obj in attachments:
            upload_tasks.append(AttachmentProcessor._upload_to_file_api(gemini_client_instance, att_obj, mime_detector_cls))
        results = await asyncio.gather(*upload_tasks, return_exceptions=True)
        for result in results:
            if isinstance(result, Exception):
                logger.error(f"❌ Attachment processing task failed: {result}", exc_info=result)
                gemini_parts.append(types.Part(text="[Attachment: Processing failed for one file]"))
            elif result is not None:
                gemini_parts.append(result)
        return gemini_parts