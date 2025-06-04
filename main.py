import asyncio
import base64
import io
import logging
import os
import re
import tempfile
import wave
import json
import aiohttp
import discord
import numpy as np
import soundfile
import magic
from datetime import datetime, timezone, timedelta
from collections import defaultdict
from typing import NamedTuple, List as TypingList
from discord.ext import commands
from dotenv import load_dotenv
from google import genai
from google.genai import types
from google.genai.chats import Chat as GenAIChatSession
logger = logging.getLogger("Bard")
class Config:
    """Stores all configuration constants for the bot."""
    load_dotenv()
    DISCORD_BOT_TOKEN = os.getenv("DISCORD_BOT_TOKEN")
    GEMINI_API_KEY = os.getenv("GEMINI_API_KEY")
    MODEL_ID = os.getenv("MODEL_ID", "gemini-2.5-flash-preview-05-20")
    MODEL_ID_TTS = os.getenv("MODEL_ID_TTS", "gemini-2.5-flash-preview-tts")
    VOICE_NAME = os.getenv("VOICE_NAME", "Kore")
    MAX_MESSAGE_LENGTH = int(os.getenv("MAX_MESSAGE_LENGTH", 2000))
    MAX_REPLY_DEPTH = int(os.getenv("MAX_REPLY_DEPTH", 10))
    THINKING_BUDGET = int(os.getenv("THINKING_BUDGET", 2048))
    MAX_OUTPUT_TOKENS = int(os.getenv("MAX_OUTPUT_TOKENS", 65536))
    TTS_SAMPLE_RATE = int(os.getenv("TTS_SAMPLE_RATE", 24000))
    TTS_CHANNELS = int(os.getenv("TTS_CHANNELS", 1))
    TTS_SAMPLE_WIDTH = int(os.getenv("TTS_SAMPLE_WIDTH", 2))
    WAVEFORM_PLACEHOLDER = os.getenv("WAVEFORM_PLACEHOLDER", "FzYACgAAAAAAACQAAAAAAAA=")
    FFMPEG_PATH = os.getenv("FFMPEG_PATH", "ffmpeg")
    PROMPT_DIR = os.getenv("PROMPT_DIR", "prompts")
    HISTORY_DIR = os.getenv("HISTORY_DIR", "history")
    MEMORY_DIR = os.getenv("MEMORY_DIR", "memories")
    MAX_HISTORY_TURNS = int(os.getenv("MAX_HISTORY_TURNS", 4))
    MAX_HISTORY_AGE = int(os.getenv("MAX_HISTORY_AGE", "0"))
    MAX_MEMORIES = int(os.getenv("MAX_MEMORIES", 32))
active_bot_responses = {}
gemini_client = None
chat_history_manager = None
memory_manager = None
intents = discord.Intents.default()
intents.message_content = True
bot = commands.Bot(command_prefix="!", intents=intents)
class HistoryEntry(NamedTuple):
    timestamp: datetime
    content: types.Content
class PromptManager:
    @staticmethod
    def load_combined_system_prompt() -> str:
        """Loads and combines all .prompt.md files from the Config.PROMPT_DIR."""
        prompt_contents = []
        prompt_dir = Config.PROMPT_DIR
        if not os.path.isdir(prompt_dir):
            logger.error(f"❌ Prompt directory not found. Using fallback system prompt.\nDirectory:\n{prompt_dir}")
            return (
                "You are a helpful AI assistant on Discord. Be concise and helpful. "
            )
        prompt_files = sorted([f for f in os.listdir(prompt_dir) if f.endswith(".prompt.md") and os.path.isfile(os.path.join(prompt_dir, f))])
        if not prompt_files:
            logger.warning(f"⚠️ No .prompt.md files found in directory. Using fallback system prompt.\nDirectory:\n{prompt_dir}")
            return (
                "You are a helpful AI assistant on Discord. Be concise and helpful. "
            )
        for filename in prompt_files:
            filepath = os.path.join(prompt_dir, filename)
            try:
                with open(filepath, "r", encoding="utf-8") as f:
                    content = f.read().strip()
                    if content:
                        prompt_contents.append(content)
                logger.info(f"📝 Successfully loaded and appended {filepath}.")
            except Exception as e:
                logger.error(f"❌ Error loading prompt from file.\nFilepath: {filepath}\nError:\n{e}", exc_info=True)
        final_prompt = "\n\n".join(prompt_contents).strip()
        if not final_prompt:
            logger.error("❌ All .prompt.md files were empty or failed to load. Using a minimal fallback system prompt.")
            return (
                "You are a helpful AI assistant on Discord. Be concise and helpful. "
            )
        logger.info(f"📝 Successfully combined {len(prompt_contents)} prompt file(s) into the system prompt.")
        return final_prompt
    @staticmethod
    def generate_per_message_metadata_header(message: discord.Message) -> str:
        """Generates the metadata header for each message sent to the AI."""
        user = message.author
        channel = message.channel
        channel_name_str = 'DM'
        guild_name_str = 'N/A (Direct Message)'
        if message.guild:
            guild_name_str = f"{message.guild.name} (ID: {message.guild.id})"
            if isinstance(channel, discord.Thread):
                channel_name_str = f"{channel.parent.name}/{channel.name} (ID: {channel.id})"
            elif hasattr(channel, 'name'):
                channel_name_str = f"{channel.name} (ID: {channel.id})"
            else:
                channel_name_str = f"Unknown Channel (ID: {channel.id})"
        else:
            channel_name_str = f"Direct Message with {user.display_name} (Channel ID: {channel.id})"
        metadata_content = f"""[DYNAMIC_CONTEXT:START]
Timestamp: {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M:%S UTC')}
Guild: {guild_name_str}
Channel: {channel_name_str}
User: {user.display_name} (@{user.name}, ID: {user.id})
User Mention: <@{user.id}>
[DYNAMIC_CONTEXT:END]"""
        return metadata_content
class ChatHistoryManager:
    def __init__(self):
        self.locks = defaultdict(asyncio.Lock)
        try:
            os.makedirs(Config.HISTORY_DIR, exist_ok=True)
            logger.info(f"💾 Chat history directory created/verified: {Config.HISTORY_DIR}")
        except OSError as e:
            logger.error(f"❌ Could not create chat history directory.\nDirectory:\n{Config.HISTORY_DIR}\nError:\n{e}", exc_info=True)
    def _get_history_filepath(self, guild_id: int | None, user_id: int | None = None) -> str:
        """
        Constructs the file path for chat history.
        For guild-level history, user_id is ignored.
        For DM history, guild_id is None, and user_id is used.
        """
        if guild_id is not None:
            filename = f"{guild_id}.history.json"
        elif user_id is not None:
            filename = f"DM_{user_id}.history.json"
        else:
            logger.error("❌ Attempted to get history filepath with neither guild_id nor user_id.")
            filename = "unknown_history.history.json"
        return os.path.join(Config.HISTORY_DIR, filename)
    async def load_history(self, guild_id: int | None, user_id: int | None = None) -> TypingList[HistoryEntry]:
        filepath = self._get_history_filepath(guild_id, user_id)
        loaded_history_entries: TypingList[HistoryEntry] = []
        async with self.locks[filepath]:
            if not os.path.exists(filepath):
                logger.info(f"💾 No history file found. Starting fresh.\nFilepath: {filepath}")
                return []
            try:
                with open(filepath, "r", encoding="utf-8") as f:
                    raw_history_list = json.load(f)
                    if not isinstance(raw_history_list, list):
                        logger.error(f"❌ History file content is not a list. Starting fresh.\nFilepath: {filepath}")
                        return []
                    for item_wrapper_dict in raw_history_list:
                        if not isinstance(item_wrapper_dict, dict) or "timestamp" not in item_wrapper_dict or "content" not in item_wrapper_dict:
                            logger.warning(f"⚠️ Skipping malformed history item wrapper in {filepath}: {item_wrapper_dict}")
                            continue
                        timestamp_str = item_wrapper_dict["timestamp"]
                        item_dict = item_wrapper_dict["content"]
                        try:
                            entry_timestamp = datetime.fromisoformat(timestamp_str.replace("Z", "+00:00"))
                            if entry_timestamp.tzinfo is None:
                                entry_timestamp = entry_timestamp.replace(tzinfo=timezone.utc)
                        except ValueError:
                            logger.warning(f"⚠️ Skipping history item with invalid timestamp format in {filepath}: {timestamp_str}")
                            continue
                        loaded_parts = []
                        for part_dict in item_dict.get("parts", []):
                            if "text" in part_dict:
                                loaded_parts.append(types.Part(text=part_dict["text"]))
                            elif "inline_data" in part_dict:
                                try:
                                    data_bytes = base64.b64decode(part_dict["inline_data"]["data"])
                                    loaded_parts.append(types.Part(inline_data=types.Blob(
                                        mime_type=part_dict["inline_data"]["mime_type"],
                                        data=data_bytes
                                    )))
                                except Exception as e_b64:
                                    logger.error(f"❌ Failed to process inline_data from history.\nError:\n{e_b64}")
                                    loaded_parts.append(types.Part(text="[Error: Could not load inline_data from history]"))
                            elif "file_data" in part_dict:
                                loaded_parts.append(types.Part(file_data=types.FileData(
                                    mime_type=part_dict["file_data"]["mime_type"],
                                    file_uri=part_dict["file_data"]["file_uri"]
                                )))
                            elif "function_call" in part_dict:
                                loaded_parts.append(types.Part(function_call=types.FunctionCall(
                                    name=part_dict["function_call"]["name"],
                                    args=part_dict["function_call"]["args"]
                                )))
                            elif "function_response" in part_dict:
                                loaded_parts.append(types.Part(function_response=types.FunctionResponse(
                                    name=part_dict["function_response"]["name"],
                                    response=part_dict["function_response"]["response"]
                                )))
                        role = item_dict.get("role", "user")
                        if role not in ("user", "model", "tool"):
                            logger.warning(f"⚠️ Invalid role found in history file: {role}. Defaulting to 'user'.")
                            role = "user"
                        reconstructed_content = types.Content(role=role, parts=loaded_parts)
                        loaded_history_entries.append(HistoryEntry(timestamp=entry_timestamp, content=reconstructed_content))
                logger.info(f"💾 Loaded {len(loaded_history_entries)} raw history entries from {filepath}.")
            except json.JSONDecodeError:
                logger.error(f"❌ Could not decode JSON from history file. Starting with fresh history for this session.\nFilepath: {filepath}")
                return []
            except Exception as e:
                logger.error(f"❌ Error loading history from file.\nFilepath: {filepath}\nError:\n{e}", exc_info=True)
                return []
        if Config.MAX_HISTORY_AGE > 0:
            now_utc = datetime.now(timezone.utc)
            min_age_delta = timedelta(minutes=Config.MAX_HISTORY_AGE)
            age_filtered_entries = [
                entry for entry in loaded_history_entries
                if (now_utc - entry.timestamp) <= min_age_delta
            ]
            if len(age_filtered_entries) < len(loaded_history_entries):
                logger.info(f"💾 History filtered by age ({Config.MAX_HISTORY_AGE} min): {len(loaded_history_entries)} -> {len(age_filtered_entries)} entries.")
            loaded_history_entries = age_filtered_entries
        max_content_entries = Config.MAX_HISTORY_TURNS * 2
        if len(loaded_history_entries) > max_content_entries:
            final_history_entries = loaded_history_entries[-max_content_entries:]
            logger.info(f"💾 History truncated by turn count: {len(loaded_history_entries)} -> {len(final_history_entries)} entries (max: {max_content_entries}).")
            loaded_history_entries = final_history_entries
        return loaded_history_entries
    async def save_history(self, guild_id: int | None, user_id: int | None, history_entries: TypingList[HistoryEntry]):
        filepath = self._get_history_filepath(guild_id, user_id)
        max_content_entries = Config.MAX_HISTORY_TURNS * 2
        if len(history_entries) > max_content_entries:
            entries_to_save = history_entries[-max_content_entries:]
        else:
            entries_to_save = history_entries
        logger.info(f"💾 Saving {len(entries_to_save)} history entries to {filepath}.")
        serializable_history_wrappers = []
        for entry in entries_to_save:
            content_item = entry.content
            parts_list = []
            for part in content_item.parts:
                if part.text is not None:
                    parts_list.append({"text": part.text})
                elif part.inline_data is not None:
                    parts_list.append({
                        "inline_data": {
                            "mime_type": part.inline_data.mime_type,
                            "data": base64.b64encode(part.inline_data.data).decode('utf-8')
                        }
                    })
                elif part.file_data is not None:
                    parts_list.append({
                        "file_data": {
                            "mime_type": part.file_data.mime_type,
                            "file_uri": part.file_data.file_uri
                        }
                    })
                elif part.function_call is not None:
                    parts_list.append({
                        "function_call": {
                            "name": part.function_call.name,
                            "args": dict(part.function_call.args) if part.function_call.args else {}
                        }
                    })
                elif part.function_response is not None:
                     parts_list.append({
                        "function_response": {
                            "name": part.function_response.name,
                            "response": dict(part.function_response.response) if part.function_response.response else {}
                        }
                    })
            content_dict = {
                "role": content_item.role,
                "parts": parts_list
            }
            entry_wrapper_dict = {
                "timestamp": entry.timestamp.isoformat(),
                "content": content_dict
            }
            serializable_history_wrappers.append(entry_wrapper_dict)
        temp_filepath = filepath + ".tmp"
        async with self.locks[filepath]:
            try:
                with open(temp_filepath, "w", encoding="utf-8") as f:
                    json.dump(serializable_history_wrappers, f, indent=2)
                os.replace(temp_filepath, filepath)
                logger.info(f"💾 History successfully saved to {filepath}.")
            except Exception as e:
                logger.error(f"❌ Error saving history to file.\nFilepath: {filepath}\nError:\n{e}", exc_info=True)
                if os.path.exists(temp_filepath):
                    try: os.remove(temp_filepath)
                    except OSError as e_rem: logger.warning(f"⚠️ Could not remove temporary history file.\nFilepath: {temp_filepath}\nError:\n{e_rem}")
    async def delete_history(self, guild_id: int | None, user_id: int | None = None):
        filepath = self._get_history_filepath(guild_id, user_id)
        deleted = False
        async with self.locks[filepath]:
            if os.path.exists(filepath):
                try:
                    os.remove(filepath)
                    logger.info(f"💾 Deleted history file.\nFilepath: {filepath}")
                    deleted = True
                except OSError as e:
                    logger.error(f"❌ Error deleting history file.\nFilepath: {filepath}\nError:\n{e}", exc_info=True)
            else:
                logger.info(f"💾 No history file found to delete.\nFilepath: {filepath}")
        return deleted
class MemoryManager:
    def __init__(self):
        self.locks = defaultdict(asyncio.Lock)
        try:
            os.makedirs(Config.MEMORY_DIR, exist_ok=True)
            logger.info(f"🧠 Memory directory created/verified: {Config.MEMORY_DIR}")
        except OSError as e:
            logger.error(f"❌ Could not create memory directory.\nDirectory:\n{Config.MEMORY_DIR}\nError:\n{e}", exc_info=True)
    def _get_memory_filepath(self, user_id: int) -> str:
        """Constructs the file path for a user's memories."""
        filename = f"{user_id}.memory.json"
        return os.path.join(Config.MEMORY_DIR, filename)
    def _generate_memory_id(self, existing_memories: list[dict]) -> int:
        """Generates a new unique memory ID."""
        if not existing_memories:
            return 1
        return max(item.get("id", 0) for item in existing_memories) + 1
    async def load_memories(self, user_id: int) -> list[dict]:
        """Loads memories for a given user_id."""
        filepath = self._get_memory_filepath(user_id)
        memories_list = []
        async with self.locks[filepath]:
            if not os.path.exists(filepath):
                logger.info(f"🧠 No memory file found for user: {user_id}.")
                return []
            try:
                with open(filepath, "r", encoding="utf-8") as f:
                    loaded_data = json.load(f)
                    if isinstance(loaded_data, list):
                        memories_list = [item for item in loaded_data if isinstance(item, dict) and "id" in item and "content" in item and "timestamp_added" in item]
                    else:
                        logger.error(f"❌ Memory file for user {user_id} is not a list. Discarding.\nFilepath: {filepath}")
                        return []
                logger.info(f"🧠 Loaded {len(memories_list)} memories for user {user_id} from {filepath}.")
            except json.JSONDecodeError:
                logger.error(f"❌ Could not decode JSON from memory file. Starting with fresh memories for this session.\nUser ID: {user_id}\nFilepath: {filepath}")
                return []
            except Exception as e:
                logger.error(f"❌ Error loading memories from file.\nUser ID: {user_id}\nFilepath: {filepath}\nError:\n{e}", exc_info=True)
                return []
        if len(memories_list) > Config.MAX_MEMORIES:
            memories_list = memories_list[-Config.MAX_MEMORIES:]
            logger.info(f"🧠 Memories for user {user_id} truncated to {len(memories_list)} entries (max: {Config.MAX_MEMORIES}).")
        return memories_list
    async def save_memories(self, user_id: int, memories: list[dict]):
        """Saves memories for a given user_id."""
        filepath = self._get_memory_filepath(user_id)
        if len(memories) > Config.MAX_MEMORIES:
            memories_to_save = memories[-Config.MAX_MEMORIES:]
        else:
            memories_to_save = memories
        logger.info(f"🧠 Saving {len(memories_to_save)} memories for user {user_id} to {filepath}")
        temp_filepath = filepath + ".tmp"
        async with self.locks[filepath]:
            try:
                with open(temp_filepath, "w", encoding="utf-8") as f:
                    json.dump(memories_to_save, f, indent=2)
                os.replace(temp_filepath, filepath)
                logger.info(f"🧠 Memories successfully saved for user {user_id} to {filepath}.")
            except Exception as e:
                logger.error(f"❌ Error saving memories to file.\nUser ID: {user_id}\nFilepath: {filepath}\nError:\n{e}", exc_info=True)
                if os.path.exists(temp_filepath):
                    try: os.remove(temp_filepath)
                    except OSError as e_rem: logger.warning(f"⚠️ Could not remove temporary memory file.\nFilepath: {temp_filepath}\nError:\n{e_rem}")
    async def add_memory(self, user_id: int, memory_content: str) -> bool:
        """Adds a new memory for the user."""
        if not memory_content.strip():
            logger.warning(f"🧠 Attempted to add empty memory for user {user_id}. Skipping.")
            return False
        memories = await self.load_memories(user_id)
        new_id = self._generate_memory_id(memories)
        timestamp = datetime.now(timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ')
        new_memory = {"id": new_id, "content": memory_content.strip(), "timestamp_added": timestamp}
        memories.append(new_memory)
        await self.save_memories(user_id, memories)
        logger.info(f"🧠 Added memory ID {new_id} for user {user_id}: '{memory_content.strip()}'")
        return True
    async def remove_memory(self, user_id: int, memory_id_to_remove: int) -> bool:
        """Removes a specific memory by its ID for the user."""
        memories = await self.load_memories(user_id)
        initial_count = len(memories)
        memories_after_removal = [mem for mem in memories if mem.get("id") != memory_id_to_remove]
        if len(memories_after_removal) < initial_count:
            await self.save_memories(user_id, memories_after_removal)
            logger.info(f"🧠 Removed memory ID {memory_id_to_remove} for user {user_id}.")
            return True
        else:
            logger.warning(f"🧠 Memory ID {memory_id_to_remove} not found for user {user_id}. No removal performed.")
            return False
    async def delete_memories(self, user_id: int) -> bool:
        """Deletes all memories for a given user_id."""
        filepath = self._get_memory_filepath(user_id)
        deleted = False
        async with self.locks[filepath]:
            if os.path.exists(filepath):
                try:
                    os.remove(filepath)
                    logger.info(f"🧠 Deleted all memories for user {user_id}. File: {filepath}")
                    deleted = True
                except OSError as e:
                    logger.error(f"❌ Error deleting memory file for user {user_id}.\nFilepath: {filepath}\nError:\n{e}", exc_info=True)
            else:
                logger.info(f"🧠 No memory file found to delete for user {user_id}.\nFilepath: {filepath}")
        return deleted
    def format_memories_for_llm(self, user_id: int, memories: list[dict]) -> str:
        """Formats loaded memories into a string block for the LLM prompt."""
        if not memories:
            return ""
        formatted_mem_parts = [f"[{user_id}:MEMORY:START]"]
        for mem in memories:
            formatted_mem_parts.append(f"ID: `{mem.get('id')}`")
            formatted_mem_parts.append(f"Recorded: `{mem.get('timestamp_added')}`")
            formatted_mem_parts.append(mem.get('content', '[Error: Memory content missing]'))
        formatted_mem_parts.append(f"[{user_id}:MEMORY:END]")
        return "\n".join(formatted_mem_parts)
class MimeDetector:
    """
    Detects MIME types from byte data using the python-magic library.
    """
    @classmethod
    def detect(cls, data: bytes) -> str:
        """
        Detects the MIME type of the given byte data using libmagic.
        Args:
            data: The byte data to inspect.
        Returns:
            The detected MIME type string, or 'application/octet-stream' if unknown or on error.
        """
        try:
            mime_type = magic.from_buffer(data, mime=True)
            if mime_type:
                logger.debug(f"🔍 MIME type detected by python-magic: {mime_type}")
                return mime_type
            else:
                logger.warning("🔍 python-magic returned an empty MIME type. Defaulting to octet-stream.")
                return 'application/octet-stream'
        except ImportError:
            logger.error("❌ python-magic library is not installed correctly or its dependency libmagic is missing. "
                         "Please ensure 'python-magic' is in requirements.txt and libmagic is installed system-wide. "
                         "Falling back to 'application/octet-stream'.")
            return 'application/octet-stream'
        except magic.MagicException as e:
            logger.error(f"❌ python-magic encountered an error (e.g., magic file not found or corrupt): {e}. "
                         "Falling back to 'application/octet-stream'.")
            return 'application/octet-stream'
        except Exception as e:
            logger.error(f"❌ Unexpected error during MIME detection with python-magic: {e}. "
                         "Falling back to 'application/octet-stream'.", exc_info=True)
            return 'application/octet-stream'
class YouTubeProcessor:
    """Extracts YouTube URLs and prepares them as FileData parts for Gemini."""
    PATTERNS = [
        re.compile(r'https?://(?:www\.)?youtube\.com/watch\?v=([\w-]+)(?:&\S+)?', re.IGNORECASE),
        re.compile(r'https?://youtu\.be/([\w-]+)(?:\?\S+)?', re.IGNORECASE),
        re.compile(r'https?://(?:www\.)?youtube\.com/embed/([\w-]+)(?:\?\S+)?', re.IGNORECASE),
        re.compile(r'https?://(?:www\.)?youtube\.com/v/([\w-]+)(?:\?\S+)?', re.IGNORECASE),
        re.compile(r'https?://(?:www\.)?youtube\.com/shorts/([\w-]+)(?:\?\S+)?', re.IGNORECASE),
    ]
    @classmethod
    def extract_urls(cls, text: str) -> list[str]:
        """Extracts all unique YouTube video URLs from a given text."""
        found_urls = []
        for pattern in cls.PATTERNS:
            matches = pattern.finditer(text)
            for match in matches:
                found_urls.append(match.group(0))
        return list(set(found_urls))
    @classmethod
    def process_content(cls, content: str) -> tuple[str, list[types.Part]]:
        """
        Extracts YouTube URLs, creates FileData parts, and returns cleaned content.
        """
        urls = cls.extract_urls(content)
        if not urls:
            return content, []
        youtube_parts = []
        for url in urls:
            try:
                youtube_parts.append(types.Part(file_data=types.FileData(mime_type="video/youtube", file_uri=url)))
            except Exception as e:
                logger.error(f"❌ Error creating FileData for YouTube URL.\nURL:\n{url}\nError:\n{e}", exc_info=True)
        cleaned_content = content
        for url in urls:
            cleaned_content = cleaned_content.replace(url, "")
        cleaned_content = re.sub(r'\s+', ' ', cleaned_content).strip()
        if youtube_parts:
            logger.info(f"🎬 Identified {len(youtube_parts)} YouTube video link(s) for model processing.\nURLs:\n{urls}")
        return cleaned_content, youtube_parts
class TTSGenerator:
    """Generates speech audio using Gemini TTS and converts it to OGG Opus."""
    @staticmethod
    async def _convert_to_ogg_opus(input_wav_path: str, output_ogg_path: str) -> bool:
        """Converts a WAV file to OGG Opus format using ffmpeg."""
        try:
            command = [
                Config.FFMPEG_PATH, '-y', '-i', input_wav_path,
                '-c:a', 'libopus', '-b:a', '32k', '-ar', '48000',
                '-ac', '1', '-application', 'voip', '-vbr', 'on', output_ogg_path
            ]
            logger.info(f"🎤 Executing ffmpeg command.\nCommand:\n{' '.join(command)}")
            process = await asyncio.create_subprocess_exec(
                *command, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE
            )
            stdout, stderr = await process.communicate()
            if process.returncode != 0:
                stdout_decoded = stdout.decode(errors='ignore')
                stderr_decoded = stderr.decode(errors='ignore')
                logger.error(f"❌ ffmpeg conversion failed for input WAV.\nInput Path:\n{input_wav_path}\nReturn Code: {process.returncode}\nStdout:\n{stdout_decoded}\nStderr:\n{stderr_decoded}")
                return False
            logger.info(f"🎤 Successfully converted WAV to OGG Opus.\nInput WAV:\n{input_wav_path}\nOutput OGG:\n{output_ogg_path}")
            return True
        except FileNotFoundError:
            logger.error(f"❌ ffmpeg command not found. Ensure FFMPEG_PATH is correct.\nAttempted Path:\n{Config.FFMPEG_PATH}")
            return False
        except Exception as e:
            logger.error(f"❌ Error during ffmpeg conversion.\nInput WAV:\n{input_wav_path}\nError:\n{e}", exc_info=True)
            return False
    @staticmethod
    def _get_audio_duration_and_waveform(audio_path: str, max_waveform_points: int = 128) -> tuple[float, str]:
        """Gets audio duration and generates a base64 encoded waveform string."""
        try:
            audio_data, samplerate = soundfile.read(audio_path)
            duration_secs = len(audio_data) / float(samplerate)
            mono_audio_data = np.mean(audio_data, axis=1) if audio_data.ndim > 1 else audio_data
            num_samples = len(mono_audio_data)
            if num_samples == 0:
                return duration_secs, Config.WAVEFORM_PLACEHOLDER
            if np.issubdtype(mono_audio_data.dtype, np.integer):
                 mono_audio_data = mono_audio_data / np.iinfo(mono_audio_data.dtype).max
            step = max(1, num_samples // max_waveform_points)
            waveform_raw_bytes = bytearray()
            for i in range(0, num_samples, step):
                chunk = mono_audio_data[i:i+step]
                if len(chunk) == 0: continue
                rms = np.sqrt(np.mean(chunk**2))
                scaled_value = int(min(rms * 5.0, 1.0) * 255)
                waveform_raw_bytes.append(scaled_value)
            if not waveform_raw_bytes:
                return duration_secs, Config.WAVEFORM_PLACEHOLDER
            waveform_b64 = base64.b64encode(waveform_raw_bytes).decode('utf-8')
            return duration_secs, waveform_b64
        except Exception as e:
            logger.error(f"❌ Error getting duration/waveform for audio file.\nFile:\n{audio_path}\nError:\n{e}", exc_info=True)
            try:
                info = soundfile.info(audio_path)
                return info.duration, Config.WAVEFORM_PLACEHOLDER
            except Exception as e_info:
                logger.error(f"❌ Fallback to get duration also failed for audio file.\nFile:\n{audio_path}\nError:\n{e_info}", exc_info=True)
                return 1.0, Config.WAVEFORM_PLACEHOLDER
    @staticmethod
    async def generate_speech_ogg(text_for_tts: str) -> tuple[bytes, float, str] | None:
        """Generates speech audio in OGG Opus format from text using Gemini TTS."""
        global gemini_client
        if not gemini_client:
            logger.error("❌ Gemini client not initialized. Cannot generate TTS.")
            return None
        tmp_wav_path, tmp_ogg_path = None, None
        try:
            logger.info(f"🎤 Generating TTS (WAV) with details:\nText:\n{text_for_tts}\nVoice: {Config.VOICE_NAME}\nModel: {Config.MODEL_ID_TTS}")
            speech_generation_config = types.GenerateContentConfig(
                response_modalities=["AUDIO"],
                speech_config=types.SpeechConfig(
                    voice_config=types.VoiceConfig(prebuilt_voice_config=types.PrebuiltVoiceConfig(voice_name=Config.VOICE_NAME))
                )
            )
            response = await gemini_client.aio.models.generate_content(
                model=Config.MODEL_ID_TTS, contents=text_for_tts, generation_config=speech_generation_config
            )
            wav_data = None
            if (response.candidates and response.candidates[0].content and
                response.candidates[0].content.parts and
                response.candidates[0].content.parts[0].inline_data and
                response.candidates[0].content.parts[0].inline_data.data):
                wav_data = response.candidates[0].content.parts[0].inline_data.data
            if not wav_data:
                logger.error("❌ No WAV audio data extracted from Gemini TTS response.")
                return None
            with tempfile.NamedTemporaryFile(suffix='.wav', delete=False) as tmp_wav_file_obj, \
                 tempfile.NamedTemporaryFile(suffix='.ogg', delete=False) as tmp_ogg_file_obj:
                tmp_wav_path, tmp_ogg_path = tmp_wav_file_obj.name, tmp_ogg_file_obj.name
            with wave.open(tmp_wav_path, 'wb') as wf:
                wf.setnchannels(Config.TTS_CHANNELS)
                wf.setsampwidth(Config.TTS_SAMPLE_WIDTH)
                wf.setframerate(Config.TTS_SAMPLE_RATE)
                wf.writeframes(wav_data)
            if not await TTSGenerator._convert_to_ogg_opus(tmp_wav_path, tmp_ogg_path): return None
            duration_secs, waveform_b64 = TTSGenerator._get_audio_duration_and_waveform(tmp_ogg_path)
            with open(tmp_ogg_path, 'rb') as f_ogg: ogg_opus_bytes = f_ogg.read()
            logger.info(f"🎤 OGG Opus generated successfully.\nSize: {len(ogg_opus_bytes)} bytes\nDuration: {duration_secs:.2f}s.")
            return ogg_opus_bytes, duration_secs, waveform_b64
        except Exception as e:
            logger.error(f"❌ TTS generation or OGG conversion pipeline error.\nError:\n{e}", exc_info=True)
            return None
        finally:
            for f_path in [tmp_wav_path, tmp_ogg_path]:
                if f_path and os.path.exists(f_path):
                    try: os.unlink(f_path)
                    except OSError as e_unlink: logger.warning(f"⚠️ Could not delete temporary file.\nFile:\n{f_path}\nError:\n{e_unlink}")
class MessageSender:
    """Handles sending messages (text and voice) to Discord."""
    @staticmethod
    async def _send_text_reply(message_to_reply_to: discord.Message, text_content: str) -> discord.Message | None:
        """Sends a text reply, handling Discord's message length limits. Returns the primary sent message."""
        primary_sent_message = None
        if not text_content or not text_content.strip():
            text_content = "I processed your request but have no further text to add."
        if len(text_content) > Config.MAX_MESSAGE_LENGTH:
            first_chunk = text_content[:Config.MAX_MESSAGE_LENGTH]
            remaining_text = text_content[Config.MAX_MESSAGE_LENGTH:]
            try:
                sent_msg = await message_to_reply_to.reply(first_chunk)
                if not primary_sent_message: primary_sent_message = sent_msg
            except discord.HTTPException as e:
                logger.error(f"❌ Failed to send reply (chunk 1). Attempting to send to channel directly.\nError:\n{e}", exc_info=True)
                try:
                    sent_msg = await message_to_reply_to.channel.send(first_chunk)
                    if not primary_sent_message: primary_sent_message = sent_msg
                except discord.HTTPException as e_chan:
                     logger.error(f"❌ Failed to send to channel directly (chunk 1).\nError:\n{e_chan}", exc_info=True)
            current_chunk = ""
            for paragraph in remaining_text.split('\n\n'):
                if len(current_chunk + paragraph + '\n\n') > Config.MAX_MESSAGE_LENGTH:
                    if current_chunk.strip():
                        try: await message_to_reply_to.channel.send(current_chunk.strip())
                        except discord.HTTPException as e: logger.error(f"❌ Failed to send subsequent message chunk.\nError:\n{e}", exc_info=True)
                    current_chunk = paragraph + '\n\n'
                else:
                    current_chunk += paragraph + '\n\n'
            if current_chunk.strip():
                try: await message_to_reply_to.channel.send(current_chunk.strip())
                except discord.HTTPException as e: logger.error(f"❌ Failed to send final message chunk.\nError:\n{e}", exc_info=True)
        else:
            try:
                sent_msg = await message_to_reply_to.reply(text_content)
                if not primary_sent_message: primary_sent_message = sent_msg
            except discord.HTTPException as e:
                logger.error(f"❌ Failed to send reply. Attempting to send to channel directly.\nError:\n{e}", exc_info=True)
                try:
                    sent_msg = await message_to_reply_to.channel.send(text_content)
                    if not primary_sent_message: primary_sent_message = sent_msg
                except discord.HTTPException as e_chan:
                    logger.error(f"❌ Failed to send to channel directly.\nError:\n{e_chan}", exc_info=True)
        if primary_sent_message:
            logger.info(f"📤 Sent text reply. Content:\n{text_content}")
        return primary_sent_message
    @staticmethod
    async def send(message_to_reply_to: discord.Message,
                     text_content: str | None,
                     audio_data: bytes | None = None,
                     duration_secs: float = 0.0,
                     waveform_b64: str = Config.WAVEFORM_PLACEHOLDER,
                     existing_bot_message_to_edit: discord.Message | None = None) -> discord.Message | None:
        """Sends a reply to a Discord message. Can be text, voice, or both."""
        can_try_native_voice = audio_data and Config.DISCORD_BOT_TOKEN and (not text_content or not text_content.strip())
        temp_ogg_file_path_for_upload = None
        if existing_bot_message_to_edit:
            if text_content and not audio_data:
                try:
                    is_simple_text_message = not existing_bot_message_to_edit.attachments and \
                                             not (existing_bot_message_to_edit.flags and existing_bot_message_to_edit.flags.value & 8192)
                    if is_simple_text_message:
                        await existing_bot_message_to_edit.edit(content=text_content[:Config.MAX_MESSAGE_LENGTH])
                        logger.info(f"✏️ Edited existing bot message with text. Content:\n{text_content}")
                        return existing_bot_message_to_edit
                except discord.HTTPException as e:
                    logger.error(f"❌ Failed to edit bot message with text. Falling back to delete and resend.\nID: {existing_bot_message_to_edit.id}\nError:\n{e}", exc_info=True)
                except Exception as e_unhandled:
                    logger.error(f"❌ Unhandled error editing bot message. Falling back to delete and resend.\nID: {existing_bot_message_to_edit.id}\nError:\n{e_unhandled}", exc_info=True)
                try: await existing_bot_message_to_edit.delete()
                except discord.HTTPException: pass
            else:
                try: await existing_bot_message_to_edit.delete()
                except discord.HTTPException: pass
        if can_try_native_voice:
            channel_id = str(message_to_reply_to.channel.id)
            try:
                with tempfile.NamedTemporaryFile(suffix='.ogg', delete=False) as temp_audio_file:
                    temp_audio_file.write(audio_data)
                    temp_ogg_file_path_for_upload = temp_audio_file.name
                async with aiohttp.ClientSession() as session:
                    upload_slot_api_url = f"https://discord.com/api/v10/channels/{channel_id}/attachments"
                    upload_slot_payload = {"files": [{"filename": "voice_message.ogg", "file_size": len(audio_data), "id": "0", "is_clip": False}]}
                    upload_slot_headers = {"Authorization": f"Bot {Config.DISCORD_BOT_TOKEN}", "Content-Type": "application/json"}
                    attachment_metadata = None
                    async with session.post(upload_slot_api_url, json=upload_slot_payload, headers=upload_slot_headers) as resp_slot:
                        if resp_slot.status == 200:
                            resp_slot_json = await resp_slot.json()
                            if resp_slot_json.get("attachments") and len(resp_slot_json["attachments"]) > 0:
                                attachment_metadata = resp_slot_json["attachments"][0]
                            else:
                                raise Exception(f"Invalid attachment slot response from Discord API. Response: {await resp_slot.text()}")
                        else:
                            raise Exception(f"Failed to get Discord upload slot. Status: {resp_slot.status}, Response: {await resp_slot.text()}")
                    put_url = attachment_metadata["upload_url"]
                    with open(temp_ogg_file_path_for_upload, 'rb') as file_to_put:
                        async with session.put(put_url, data=file_to_put, headers={'Content-Type': 'audio/ogg'}) as resp_put:
                            if resp_put.status != 200:
                                raise Exception(f"Failed to PUT audio to Discord CDN. Status: {resp_put.status}, Response: {await resp_put.text()}")
                    discord_cdn_filename = attachment_metadata["upload_filename"]
                    send_message_api_url = f"https://discord.com/api/v10/channels/{channel_id}/messages"
                    send_message_payload = {
                        "content": "",
                        "flags": 8192,
                        "attachments": [{
                            "id": "0",
                            "filename": "voice_message.ogg",
                            "uploaded_filename": discord_cdn_filename,
                            "duration_secs": round(duration_secs, 2),
                            "waveform": waveform_b64
                        }],
                        "message_reference": {"message_id": str(message_to_reply_to.id)},
                        "allowed_mentions": {"parse": [], "replied_user": False}
                    }
                    send_message_headers = {"Authorization": f"Bot {Config.DISCORD_BOT_TOKEN}", "Content-Type": "application/json"}
                    async with session.post(send_message_api_url, json=send_message_payload, headers=send_message_headers) as resp_send:
                        if resp_send.status == 200 or resp_send.status == 201:
                            response_data = await resp_send.json()
                            message_id = response_data.get("id")
                            if message_id:
                                try:
                                    sent_message = await message_to_reply_to.channel.fetch_message(message_id)
                                    logger.info(f"🎤 Sent native Discord voice message. ID: {sent_message.id} To: {message_to_reply_to.author.name} In Channel: #{message_to_reply_to.channel}")
                                    return sent_message
                                except discord.HTTPException:
                                    logger.warning("🎤 Sent native voice message, but failed to fetch the discord.Message object afterwards.")
                            return None
                        else:
                            raise Exception(f"Discord API send voice message failed. Status: {resp_send.status}, Response: {await resp_send.text()}")
            except Exception as e:
                logger.error(f"❌ Error sending native Discord voice message. Falling back to file upload or text.\nError:\n{e}", exc_info=True)
                if temp_ogg_file_path_for_upload and os.path.exists(temp_ogg_file_path_for_upload):
                    try:
                        discord_file = discord.File(temp_ogg_file_path_for_upload, "voice_response.ogg")
                        fallback_msg = await message_to_reply_to.reply(file=discord_file)
                        logger.info(f"📎 Sent voice response as .ogg file attachment (fallback). ID: {fallback_msg.id}")
                        if text_content and text_content.strip():
                             await MessageSender._send_text_reply(message_to_reply_to, text_content)
                        return fallback_msg
                    except Exception as fallback_e:
                        logger.error(f"❌ Fallback .ogg file send also failed.\nError:\n{fallback_e}", exc_info=True)
            finally:
                if temp_ogg_file_path_for_upload and os.path.exists(temp_ogg_file_path_for_upload):
                    try: os.unlink(temp_ogg_file_path_for_upload)
                    except OSError: pass
            if text_content and text_content.strip():
                return await MessageSender._send_text_reply(message_to_reply_to, text_content)
            return None
        sent_text_message = None
        if text_content and text_content.strip():
            sent_text_message = await MessageSender._send_text_reply(message_to_reply_to, text_content)
        sent_audio_file_message = None
        if audio_data and not can_try_native_voice:
            temp_ogg_path_regular = None
            try:
                with tempfile.NamedTemporaryFile(suffix='.ogg', delete=False) as temp_audio_file:
                    temp_audio_file.write(audio_data)
                    temp_ogg_path_regular = temp_audio_file.name
                discord_file = discord.File(temp_ogg_path_regular, "voice_response.ogg")
                if sent_text_message:
                    sent_audio_file_message = await message_to_reply_to.channel.send(file=discord_file)
                else:
                    sent_audio_file_message = await message_to_reply_to.reply(file=discord_file)
                if sent_audio_file_message:
                    logger.info(f"📎 Sent voice response as .ogg file attachment. ID: {sent_audio_file_message.id}")
            except Exception as e:
                logger.error(f"❌ Failed to send .ogg file as attachment.\nError:\n{e}", exc_info=True)
            finally:
                if temp_ogg_path_regular and os.path.exists(temp_ogg_path_regular):
                    try: os.unlink(temp_ogg_path_regular)
                    except OSError: pass
        return sent_text_message or sent_audio_file_message
class AttachmentProcessor:
    """Downloads Discord attachments and prepares them for Gemini."""
    _gemini_file_cache: dict[int, types.FileData] = {}
    _attachment_upload_locks: defaultdict[int, asyncio.Lock] = defaultdict(asyncio.Lock)
    @staticmethod
    async def _download_and_prepare_attachment(attachment: discord.Attachment) -> tuple[io.BytesIO, str, str] | None:
        try:
            async with aiohttp.ClientSession() as session:
                async with session.get(attachment.url) as response:
                    if response.status == 200:
                        data = await response.read()
                        mime_type = attachment.content_type
                        if not mime_type or mime_type == 'application/octet-stream':
                            mime_type = MimeDetector.detect(data)
                        return io.BytesIO(data), mime_type, attachment.filename
                    else:
                        logger.warning(f"⚠️ Failed to download attachment.\nFilename: {attachment.filename}\nURL: {attachment.url}\nHTTP Status: {response.status}")
                        return None
        except Exception as e:
            logger.error(f"❌ Error downloading attachment.\nFilename: {attachment.filename}\nURL: {attachment.url}\nError:\n{e}", exc_info=True)
            return None
    @staticmethod
    async def _upload_to_file_api(attachment: discord.Attachment) -> types.Part:
        global gemini_client, logger
        if not gemini_client:
            logger.error("❌ Gemini client not initialized. Cannot process attachment for File API.")
            return types.Part(text=f"[Attachment: {attachment.filename} - Gemini client not ready for upload]")
        cached_file_data = AttachmentProcessor._gemini_file_cache.get(attachment.id)
        if cached_file_data:
            logger.info(f"📎 Cache HIT for attachment ID {attachment.id}. Using URI: {cached_file_data.file_uri}")
            return types.Part(file_data=cached_file_data)
        lock = AttachmentProcessor._attachment_upload_locks[attachment.id]
        async with lock:
            cached_file_data_in_lock = AttachmentProcessor._gemini_file_cache.get(attachment.id)
            if cached_file_data_in_lock:
                logger.info(f"📎 Cache HIT (after lock) for attachment ID {attachment.id}. Using URI: {cached_file_data_in_lock.file_uri}")
                return types.Part(file_data=cached_file_data_in_lock)
            logger.info(f"📎 Cache MISS for attachment ID {attachment.id}. Proceeding with download and upload.")
            prepared_data = await AttachmentProcessor._download_and_prepare_attachment(attachment)
            if not prepared_data:
                return types.Part(text=f"[Attachment: {attachment.filename} - Download or preparation failed.]")
            file_io, mime, fname = prepared_data
            try:
                file_io.seek(0)
                uploaded_gemini_file_obj = await gemini_client.aio.files.upload(
                    file=file_io,
                    config=types.UploadFileConfig(mime_type=mime, display_name=fname)
                )
                logger.info(f"📎 Successfully uploaded file to Gemini File API.\nDisplay Name: {fname}\nMIME Type: {mime}\nFile URI: {uploaded_gemini_file_obj.uri}")
                new_file_data_to_cache = types.FileData(
                    mime_type=uploaded_gemini_file_obj.mime_type,
                    file_uri=uploaded_gemini_file_obj.uri
                )
                AttachmentProcessor._gemini_file_cache[attachment.id] = new_file_data_to_cache
                logger.info(f"📎 Successfully CACHED attachment ID {attachment.id}. URI: {new_file_data_to_cache.file_uri}")
                return types.Part(file_data=new_file_data_to_cache)
            except Exception as e:
                logger.error(f"❌ Error uploading file to Gemini File API.\nDisplay Name: {fname}\nMIME Type: {mime}\nError:\n{e}", exc_info=True)
                return types.Part(text=f"[Attachment: {fname} - Gemini File API Upload failed. Error: {str(e)}]")
    @staticmethod
    async def process_discord_attachments(attachments: list[discord.Attachment]) -> list[types.Part]:
        parts: list[types.Part] = []
        if not attachments:
            return parts
        for attachment_obj in attachments:
            file_part = await AttachmentProcessor._upload_to_file_api(attachment_obj)
            parts.append(file_part)
        return parts
class ReplyChainProcessor:
    """Processes message reply chains to provide context to the LLM."""
    @staticmethod
    async def get_chain(message: discord.Message) -> list[dict]:
        chain = []
        current_msg_obj = message
        depth = 0
        while current_msg_obj and depth < Config.MAX_REPLY_DEPTH:
            msg_info = {
                'message_obj': current_msg_obj,
                'author_name': f"{current_msg_obj.author.display_name} (@{current_msg_obj.author.name})",
                'author_id': current_msg_obj.author.id,
                'is_bot': current_msg_obj.author.bot,
                'content': current_msg_obj.content,
                'attachments': list(current_msg_obj.attachments),
            }
            chain.insert(0, msg_info)
            if hasattr(current_msg_obj, 'reference') and current_msg_obj.reference and current_msg_obj.reference.message_id:
                try:
                    current_msg_obj = await current_msg_obj.channel.fetch_message(current_msg_obj.reference.message_id)
                    depth += 1
                except (discord.NotFound, discord.Forbidden, discord.HTTPException):
                    break
            else:
                break
        return chain
    @staticmethod
    def format_context_for_llm(chain: list[dict], current_message_id: int, bot_user_id: int) -> str:
        if len(chain) <= 1: return ""
        context_str = "\n[REPLY_CONTEXT:START]\n"
        for msg_data in chain:
            if msg_data['message_obj'].id == current_message_id:
                continue
            role = "User"
            if msg_data['is_bot']:
                role = "Assistant (You)" if msg_data['author_id'] == bot_user_id else "Assistant (Other Bot)"
            context_str += f"{role} ({msg_data['author_name']}): {msg_data['content']}"
            if msg_data['attachments']:
                attachment_desc = ", ".join([f"{att.filename} ({att.content_type or 'unknown type'})" for att in msg_data['attachments']])
                context_str += f" [Attachments noted: {attachment_desc}]"
            context_str += "\n"
        context_str += "[REPLY_CONTEXT:END]\n"
        return context_str.strip()
class GeminiConfigManager:
    """Manages the generation configuration for Gemini API calls."""
    @staticmethod
    def create_main_config(system_instruction_str: str) -> types.GenerateContentConfig:
        """
        Creates the Gemini generation configuration for the main chat interaction,
        enabling only custom functions.
        """
        speak_message_func = types.FunctionDeclaration(
            name="speak_message",
            description=(
                "Generates an audio voice message from the provided text, optionally with a specified speaking style. "
                "This function should be called if the user explicitly asks for a voice/audio response, or if a spoken "
                "response is most appropriate. The text provided to be spoken will NOT appear in your chat response to the user; "
                "your chat response should be separate and only contain acknowledgements or related information, not the spoken text itself."
            ),
            parameters=types.Schema(
                type=types.Type.OBJECT,
                properties={
                    "text_to_speak": types.Schema(
                        type=types.Type.STRING,
                        description="The exact and complete text content to be converted into a voice message. This text will NOT be part of your regular chat reply."
                    ),
                    "style": types.Schema(
                        type=types.Type.STRING,
                        description="Optional. The speaking style (e.g., CHEERFUL, SAD, ANGRY, EXCITED, FRIENDLY, HOPEFUL, POLITE, SERIOUS, SOMBER, WHISPERING). If omitted, a neutral voice is used."
                    ),
                },
                required=["text_to_speak"],
            )
        )
        add_user_memory_func = types.FunctionDeclaration(
            name="add_user_memory",
            description=(
                "Stores a piece of information (memory) about the user that they have stated or implied. "
                "After calling this, formulate a chat response acknowledging the memory was saved."
            ),
            parameters=types.Schema(
                type=types.Type.OBJECT,
                properties={
                    "memory_content": types.Schema(
                        type=types.Type.STRING,
                        description="The textual content of the memory to be saved for the user."
                    ),
                },
                required=["memory_content"],
            )
        )
        remove_user_memory_func = types.FunctionDeclaration(
            name="remove_user_memory",
            description=(
                "Removes a previously stored memory for the user, identified by its ID. Memory IDs are provided when listing memories "
                "or can be inferred from context. After calling this, formulate a chat response acknowledging the memory was removed."
            ),
            parameters=types.Schema(
                type=types.Type.OBJECT,
                properties={
                    "memory_id": types.Schema(
                        type=types.Type.INTEGER,
                        description="The unique numerical identifier of the memory to be removed."
                    ),
                },
                required=["memory_id"],
            )
        )
        use_built_in_tools_func = types.FunctionDeclaration(
            name="use_built_in_tools",
            description=(
                "Accesses built-in capabilities like Google Search or analyzing web URL content. "
                "Call this function if you need up-to-date information, to browse a webpage, or perform similar online tasks. "
                "The system will pass the original user request to these tools. You will receive the result back to formulate a response. "
                "This function takes no specific arguments from you; the context is handled by the system."
            ),
            parameters=types.Schema(type=types.Type.OBJECT, properties={})
        )
        custom_functions_tool = types.Tool(
            function_declarations=[
                speak_message_func,
                add_user_memory_func,
                remove_user_memory_func,
                use_built_in_tools_func,
            ]
        )
        config = types.GenerateContentConfig(
            system_instruction=system_instruction_str,
            temperature=1.0,
            top_p=0.95,
            max_output_tokens=Config.MAX_OUTPUT_TOKENS,
            safety_settings=[
                types.SafetySetting(category=cat, threshold=types.HarmBlockThreshold.BLOCK_NONE)
                for cat in [
                    types.HarmCategory.HARM_CATEGORY_HATE_SPEECH,
                    types.HarmCategory.HARM_CATEGORY_DANGEROUS_CONTENT,
                    types.HarmCategory.HARM_CATEGORY_SEXUALLY_EXPLICIT,
                    types.HarmCategory.HARM_CATEGORY_HARASSMENT
                ]
            ],
            tools=[custom_functions_tool],
            thinking_config=types.ThinkingConfig(
                include_thoughts=False,
                thinking_budget=Config.THINKING_BUDGET
            )
        )
        logger.debug(f"⚙️ Created MAIN Gemini GenerateContentConfig with custom functions. System instruction: {len(system_instruction_str)} chars.")
        return config
    @staticmethod
    def create_tooling_config(system_instruction_str: str) -> types.GenerateContentConfig:
        """
        Creates the Gemini generation configuration for the internal tooling call,
        enabling built-in tools like Google Search.
        """
        google_search_tool = types.Tool(google_search_retrieval={})
        config = types.GenerateContentConfig(
            system_instruction="ALWAYS USE GOOGLE SEARCH AND URL CONTEXT TOOLS",
            temperature=1.0,
            top_p=0.95,
            max_output_tokens=Config.MAX_OUTPUT_TOKENS,
            safety_settings=[
                types.SafetySetting(category=cat, threshold=types.HarmBlockThreshold.BLOCK_NONE)
                for cat in [
                    types.HarmCategory.HARM_CATEGORY_HATE_SPEECH,
                    types.HarmCategory.HARM_CATEGORY_DANGEROUS_CONTENT,
                    types.HarmCategory.HARM_CATEGORY_SEXUALLY_EXPLICIT,
                    types.HarmCategory.HARM_CATEGORY_HARASSMENT
                ]
            ],
            tools=[types.Tool(google_search=types.GoogleSearch()), types.Tool(url_context=types.UrlContext())],
            thinking_config=types.ThinkingConfig(
                include_thoughts=False,
                thinking_budget=Config.THINKING_BUDGET
            )
        )
        logger.debug(f"⚙️ Created TOOLING Gemini GenerateContentConfig with built-in tools. System instruction: {len(system_instruction_str)} chars.")
        return config
class ResponseExtractor:
    """Extracts text content from various Gemini API response structures."""
    @staticmethod
    def extract_text(response: any) -> str:
        """Attempts to extract textual content from a Gemini API response."""
        try:
            if hasattr(response, 'text') and response.text and isinstance(response.text, str):
                return response.text.strip()
        except ValueError:
            pass
        try:
            if response.candidates and response.candidates[0].content and response.candidates[0].content.parts:
                texts = []
                for part in response.candidates[0].content.parts:
                    if hasattr(part, 'text') and part.text:
                        texts.append(part.text)
                if texts:
                    return '\n'.join(texts).strip()
        except (AttributeError, IndexError, ValueError) as e:
            logger.debug(f"🔍 Could not extract text using primary candidate path.\nError:\n{e}")
        try:
            if hasattr(response, 'parts') and response.parts:
                texts = [p.text for p in response.parts if hasattr(p, 'text') and p.text]
                if texts:
                    return '\n'.join(texts).strip()
        except (AttributeError, ValueError):
            pass
        logger.error(f"❌ Failed to extract text from Gemini response.\nType:\n{type(response)}\nFull Response:\n{str(response)[:500]}")
        return "I encountered an issue processing the response format from the AI."
class MessageProcessor:
    """Core class for processing incoming Discord messages and interacting with Gemini."""
    @staticmethod
    async def _build_gemini_prompt_parts(message: discord.Message, metadata_header_str: str, cleaned_content: str, reply_chain_data: list[dict]) -> tuple[list[types.Part | str], bool]:
        """
        Constructs the list of parts (metadata, memories, text, files, YouTube) to send to Gemini.
        Returns a tuple containing the list of parts and a boolean indicating if the parts
        (beyond initial metadata and memories) are substantively empty.
        """
        global memory_manager
        parts: list[types.Part | str] = [types.Part(text=metadata_header_str)]
        logger.debug(f"💬 Building message parts for Gemini.\nCurrent message content (cleaned):\n{cleaned_content}")
        user_id_for_memory = message.author.id
        user_memories_list = await memory_manager.load_memories(user_id_for_memory)
        formatted_memories_str = memory_manager.format_memories_for_llm(user_id_for_memory, user_memories_list)
        if formatted_memories_str:
            parts.append(types.Part(text=formatted_memories_str))
            logger.info(f"🧠 Injected {len(user_memories_list)} memories for user {user_id_for_memory} into prompt.")
        textual_reply_context = ""
        if reply_chain_data:
            textual_reply_context = ReplyChainProcessor.format_context_for_llm(reply_chain_data, message.id, bot.user.id if bot.user else 0)
            if textual_reply_context.strip():
                parts.append(types.Part(text=textual_reply_context))
        if message.reference and message.reference.message_id and len(reply_chain_data) > 1:
            replied_to_msg_data_index = -2
            if len(reply_chain_data) == 2 and reply_chain_data[0]['message_obj'].id == message.reference.message_id:
                replied_to_msg_data_index = 0
            if 0 <= replied_to_msg_data_index < len(reply_chain_data) or \
               (replied_to_msg_data_index < 0 and abs(replied_to_msg_data_index) <= len(reply_chain_data)):
                replied_to_msg_data = reply_chain_data[replied_to_msg_data_index]
                if replied_to_msg_data['author_id'] != (bot.user.id if bot.user else 0) and replied_to_msg_data['attachments']:
                    logger.info(f"📎 Processing {len(replied_to_msg_data['attachments'])} attachment(s) from replied-to message.\nMessage ID: {replied_to_msg_data['message_obj'].id}")
                    replied_attachments_parts = await AttachmentProcessor.process_discord_attachments(replied_to_msg_data['attachments'])
                    if replied_attachments_parts: parts.extend(p for p in replied_attachments_parts if p)
            else:
                 logger.warning(f"⚠️ Could not reliably determine replied-to message for attachment processing. Chain length: {len(reply_chain_data)}")
        content_after_youtube, youtube_file_data_parts = YouTubeProcessor.process_content(cleaned_content)
        if youtube_file_data_parts: parts.extend(youtube_file_data_parts)
        current_message_text_placeholder = "User Message:"
        if content_after_youtube.strip():
            parts.append(types.Part(text=f"{current_message_text_placeholder} {content_after_youtube.strip()}"))
        else:
            if message.attachments or youtube_file_data_parts:
                 parts.append(types.Part(text=f"{current_message_text_placeholder} [See attached files or provided links]"))
        if message.attachments:
            logger.info(f"📎 Processing {len(message.attachments)} attachment(s) from current message.")
            current_message_attachment_parts = await AttachmentProcessor.process_discord_attachments(list(message.attachments))
            if current_message_attachment_parts: parts.extend(p for p in current_message_attachment_parts if p)
        final_parts = [p for p in parts if p and not (isinstance(p, str) and not p.strip())]
        temp_final_parts = []
        for p_item_idx, p_item_val in enumerate(final_parts):
            is_standalone_placeholder = False
            if isinstance(p_item_val, types.Part) and p_item_val.text:
                 is_placeholder_text = p_item_val.text.startswith(current_message_text_placeholder) and \
                                      not p_item_val.text.replace(current_message_text_placeholder, "").replace("[See attached files or provided links]", "").strip()
                 if is_placeholder_text:
                     has_data_after = False
                     for subsequent_part in final_parts[p_item_idx+1:]:
                         if isinstance(subsequent_part, types.Part) and (subsequent_part.file_data or subsequent_part.inline_data):
                             has_data_after = True
                             break
                     if not has_data_after and not message.attachments and not youtube_file_data_parts:
                         is_standalone_placeholder = True
            if not is_standalone_placeholder:
                temp_final_parts.append(p_item_val)
        final_parts = temp_final_parts
        is_substantively_empty_beyond_context = True
        has_substantive_content_after_initial_context = False
        initial_context_part_count = 1
        if formatted_memories_str: initial_context_part_count += 1
        if textual_reply_context.strip(): initial_context_part_count +=1
        for i, part_item in enumerate(final_parts):
            if i < initial_context_part_count:
                continue
            if isinstance(part_item, types.Part):
                if part_item.text and part_item.text.strip() and not part_item.text.startswith("[Attachment:"):
                    is_only_placeholder_text = part_item.text.startswith(current_message_text_placeholder) and \
                                              not part_item.text.replace(current_message_text_placeholder, "").replace("[See attached files or provided links]", "").strip()
                    if is_only_placeholder_text:
                        has_any_file_data_in_request = any(
                            p.file_data or p.inline_data for p_idx, p in enumerate(final_parts)
                            if p_idx >= initial_context_part_count and isinstance(p, types.Part)
                        )
                        if has_any_file_data_in_request:
                            has_substantive_content_after_initial_context = True; break
                    else:
                        has_substantive_content_after_initial_context = True; break
                if part_item.file_data or part_item.inline_data:
                    has_substantive_content_after_initial_context = True; break
            elif isinstance(part_item, str) and part_item.strip() and not part_item.startswith("[Attachment:"):
                 has_substantive_content_after_initial_context = True; break
        is_substantively_empty_beyond_context = not has_substantive_content_after_initial_context
        if is_substantively_empty_beyond_context:
             logger.warning("⚠️ No substantive parts (beyond metadata, memories, and reply context) were built for Gemini.")
        else:
            logger.info(f"💬 Final assembled parts for Gemini (Count: {len(final_parts)}):")
            for i, part_item in enumerate(final_parts):
                log_preview = ""
                part_type_str = ""
                if isinstance(part_item, str):
                    log_preview = part_item
                    part_type_str = "Raw String"
                elif isinstance(part_item, types.Part):
                    if part_item.text:
                        log_preview = part_item.text
                        part_type_str = "Text Part"
                    elif part_item.file_data:
                        log_preview = f"URI: {part_item.file_data.file_uri}, MIME: {part_item.file_data.mime_type}"
                        part_type_str = "FileData Part"
                    elif part_item.inline_data:
                        log_preview = f"MIME: {part_item.inline_data.mime_type}, Size: {len(part_item.inline_data.data)} bytes"
                        part_type_str = "InlineData Part"
                    else:
                        log_preview = str(part_item)
                        part_type_str = "Other Part Type"
                logger.info(f"  Part {i+1} [{part_type_str}]:\n{log_preview}")
        return final_parts, is_substantively_empty_beyond_context
    @staticmethod
    async def process(message: discord.Message, bot_message_to_edit: discord.Message | None = None):
        global chat_history_manager, memory_manager, gemini_client
        content_for_llm = re.sub(r'<[@#&!][^>]+>', '', message.content).strip()
        guild_id_for_history = message.guild.id if message.guild else None
        user_id_for_dm_history = message.author.id if guild_id_for_history is None else None
        user_id_for_memory = message.author.id
        reset_command_str = f"{bot.command_prefix}reset"
        forget_command_str = f"{bot.command_prefix}forget"
        if message.content.strip().lower().startswith(reset_command_str):
            deleted_count_msg = "No active chat history found to clear."
            if await chat_history_manager.delete_history(guild_id_for_history, user_id_for_dm_history):
                deleted_count_msg = f"🧹 Chat history has been cleared!"
            bot_response_message = await MessageSender.send(message, deleted_count_msg, None)
            if bot_response_message: active_bot_responses[message.id] = bot_response_message
            return
        if message.content.strip().lower().startswith(forget_command_str):
            deleted_mem_msg = "No memories found for you to forget."
            if await memory_manager.delete_memories(user_id_for_memory):
                deleted_mem_msg = f"🧠 All your memories with me have been forgotten, {message.author.display_name}."
            bot_response_message = await MessageSender.send(message, deleted_mem_msg, None)
            if bot_response_message: active_bot_responses[message.id] = bot_response_message
            return
        async with message.channel.typing():
            try:
                loaded_history_entries: TypingList[HistoryEntry] = await chat_history_manager.load_history(guild_id_for_history, user_id_for_dm_history)
                history_for_gemini_session: TypingList[types.Content] = []
                if loaded_history_entries: # Check if there's any history loaded
                    history_for_gemini_session = [
                        entry.content for entry in loaded_history_entries
                        if entry.content.role in ("user", "model")
                    ]
                if len(history_for_gemini_session) != len(loaded_history_entries):
                    logger.info(f"💾 Filtered history for chat session: {len(loaded_history_entries)} raw entries -> {len(history_for_gemini_session)} user/model entries for session init.")
                current_session_history_entries: TypingList[HistoryEntry] = list(loaded_history_entries)
                combined_system_prompt = PromptManager.load_combined_system_prompt()
                gemini_main_gen_config = GeminiConfigManager.create_main_config(combined_system_prompt)
                current_chat_session: GenAIChatSession = gemini_client.aio.chats.create(
                    model=Config.MODEL_ID,
                    history=history_for_gemini_session,
                    config=gemini_main_gen_config,
                )
                metadata_header = PromptManager.generate_per_message_metadata_header(message)
                reply_chain_data = await ReplyChainProcessor.get_chain(message)
                raw_gemini_parts_for_prompt, is_substantively_empty_beyond_context = await MessageProcessor._build_gemini_prompt_parts(
                    message, metadata_header, content_for_llm, reply_chain_data
                )
                if is_substantively_empty_beyond_context and not history_for_gemini_session:
                    logger.info("💬 Message content was substantively empty (beyond context), and no history. Sending default greeting.")
                    bot_response_msg = await MessageSender.send(message,"Hello! How can I help you today?",None,existing_bot_message_to_edit=bot_message_to_edit)
                    if bot_response_msg: active_bot_responses[message.id] = bot_response_msg
                    return
                api_parts_for_user_turn: list[types.Part] = []
                for p_item in raw_gemini_parts_for_prompt:
                    if isinstance(p_item, str): api_parts_for_user_turn.append(types.Part(text=p_item))
                    elif isinstance(p_item, types.Part): api_parts_for_user_turn.append(p_item)
                if not api_parts_for_user_turn :
                    logger.error("❌ All prompt parts for user turn were unexpectedly skipped or invalid. Aborting.")
                    error_reply = await MessageSender.send(message, "❌ I couldn't prepare your request properly.", None, existing_bot_message_to_edit=bot_message_to_edit)
                    if error_reply: active_bot_responses[message.id] = error_reply
                    return
                user_turn_content_for_session_and_tooling = types.Content(role="user", parts=api_parts_for_user_turn)
                current_session_history_entries.append(
                    HistoryEntry(timestamp=datetime.now(timezone.utc), content=user_turn_content_for_session_and_tooling)
                )
                logger.info(f"🧠 Sending {len(user_turn_content_for_session_and_tooling.parts)} parts to Gemini (Main Call). History length: {len(history_for_gemini_session)}.")
                response_from_gemini = await current_chat_session.send_message(
                    user_turn_content_for_session_and_tooling.parts,
                )
                text_from_gemini_for_discord_parts = []
                function_calls_to_execute = []
                executed_function_responses_for_gemini = []
                ogg_audio_data, audio_duration, audio_waveform_b64 = None, 0.0, Config.WAVEFORM_PLACEHOLDER
                if response_from_gemini.candidates and response_from_gemini.candidates[0].content:
                    initial_model_response_content = response_from_gemini.candidates[0].content
                    if initial_model_response_content.role != "model":
                        initial_model_response_content = types.Content(role="model", parts=initial_model_response_content.parts)
                    current_session_history_entries.append(
                        HistoryEntry(timestamp=datetime.now(timezone.utc), content=initial_model_response_content)
                    )
                    for part in initial_model_response_content.parts:
                        if part.function_call:
                            logger.info(f"💡 Model suggested function call: {part.function_call.name}")
                            function_calls_to_execute.append(part.function_call)
                        elif part.text:
                            text_from_gemini_for_discord_parts.append(part.text)
                else:
                    logger.error("❌ No valid content in Gemini's initial response (Main Call).")
                    text_from_gemini_for_discord_parts.append("[Error: AI did not provide an initial response.]")
                if function_calls_to_execute:
                    logger.info(f"⚙️ Model requested {len(function_calls_to_execute)} function call(s). Executing...")
                    tool_call_parts_for_history = []
                    for fc_obj in function_calls_to_execute:
                        function_name = fc_obj.name
                        args = dict(fc_obj.args) if fc_obj.args else {}
                        logger.info(f"  📞 Executing function: {function_name} with args: {args}")
                        tool_call_parts_for_history.append(types.Part(function_call=fc_obj))
                        if function_name == "speak_message":
                            text_to_speak_arg = args.get("text_to_speak")
                            style_arg = args.get("style")
                            if text_to_speak_arg:
                                tts_prompt = f"In a {style_arg.replace('_', ' ').lower()} tone, say: {text_to_speak_arg}" if style_arg else text_to_speak_arg
                                tts_result = await TTSGenerator.generate_speech_ogg(tts_prompt)
                                if tts_result:
                                    ogg_audio_data, audio_duration, audio_waveform_b64 = tts_result
                                    logger.info("🎤 TTS successful for 'speak_message'.")
                                else:
                                    logger.warning(f"🎤 TTS failed for 'speak_message'. Intended text: '{text_to_speak_arg}'")
                                    text_from_gemini_for_discord_parts.insert(0, f"[Notice: TTS failed for: \"{text_to_speak_arg}\"]")
                            else:
                                logger.warning("🧠 'speak_message' called without 'text_to_speak'.")
                        elif function_name == "add_user_memory":
                            content_arg = args.get("memory_content")
                            if content_arg:
                                success = await memory_manager.add_memory(user_id_for_memory, content_arg)
                                func_resp = types.Part.from_function_response(
                                    name=function_name, response={"success": success, "action": "added", "preview": content_arg[:30]}
                                )
                                executed_function_responses_for_gemini.append(func_resp)
                                tool_call_parts_for_history.append(func_resp)
                            else:
                                logger.warning("🧠 'add_user_memory' called without 'memory_content'.")
                                func_resp = types.Part.from_function_response(
                                    name=function_name, response={"success": False, "error": "Missing memory_content"}
                                )
                                executed_function_responses_for_gemini.append(func_resp)
                                tool_call_parts_for_history.append(func_resp)
                        elif function_name == "remove_user_memory":
                            id_arg = args.get("memory_id")
                            try:
                                mem_id = int(id_arg)
                                success = await memory_manager.remove_memory(user_id_for_memory, mem_id)
                                func_resp = types.Part.from_function_response(
                                    name=function_name, response={"success": success, "action": "removed", "id": mem_id}
                                )
                                executed_function_responses_for_gemini.append(func_resp)
                                tool_call_parts_for_history.append(func_resp)
                            except (ValueError, TypeError):
                                logger.warning(f"🧠 'remove_user_memory' called with invalid 'memory_id': {id_arg}.")
                                func_resp = types.Part.from_function_response(
                                    name=function_name, response={"success": False, "error": f"Invalid memory_id: {id_arg}"}
                                )
                                executed_function_responses_for_gemini.append(func_resp)
                                tool_call_parts_for_history.append(func_resp)
                        elif function_name == "use_built_in_tools":
                            logger.info("🛠️ Performing secondary Gemini call for built-in tools.")
                            tooling_gen_config = GeminiConfigManager.create_tooling_config(combined_system_prompt)
                            try:
                                contents_for_tooling_call: list[types.Content] = []
                                contents_for_tooling_call.extend(history_for_gemini_session)
                                contents_for_tooling_call.append(user_turn_content_for_session_and_tooling)
                                logger.info(f"🛠️ Tooling call 'contents' will have {len(contents_for_tooling_call)} Content items (history + current).")
                                for i, content_item in enumerate(contents_for_tooling_call):
                                    logger.debug(f"  Tooling Content {i} Role: {content_item.role}, Parts: {len(content_item.parts)}")
                                    tooling_response = await gemini_client.aio.models.generate_content(
                                        model=Config.MODEL_ID,
                                        contents=contents_for_tooling_call,
                                        config=tooling_gen_config,
                                    )
                                tooling_text_result = ResponseExtractor.extract_text(tooling_response)
                                logger.info(f"🛠️ Built-in tools call result: {tooling_text_result[:200]}{'...' if len(tooling_text_result) > 200 else ''}")
                                func_resp = types.Part.from_function_response(
                                    name=function_name, response={"tool_output": tooling_text_result if tooling_text_result else "No textual output from tools."}
                                )
                                executed_function_responses_for_gemini.append(func_resp)
                                tool_call_parts_for_history.append(func_resp)
                            except Exception as e_tool:
                                logger.error(f"❌ Error during 'use_built_in_tools' secondary API call: {e_tool}", exc_info=True)
                                func_resp = types.Part.from_function_response(
                                    name=function_name, response={"success": False, "error": f"Tooling call failed: {str(e_tool)}"}
                                )
                                executed_function_responses_for_gemini.append(func_resp)
                                tool_call_parts_for_history.append(func_resp)
                        else:
                            logger.warning(f"🧠 Model called unknown function: {function_name}")
                            func_resp = types.Part.from_function_response(
                                name=function_name, response={"success": False, "error": "Unknown function"}
                            )
                            executed_function_responses_for_gemini.append(func_resp)
                            tool_call_parts_for_history.append(func_resp)
                    if tool_call_parts_for_history:
                         current_session_history_entries.append(
                            HistoryEntry(timestamp=datetime.now(timezone.utc), content=types.Content(role="tool", parts=tool_call_parts_for_history))
                        )
                    if executed_function_responses_for_gemini:
                        logger.info(f"⚙️ Sending {len(executed_function_responses_for_gemini)} function execution results back to Gemini (Main Call).")
                        response_after_functions = await current_chat_session.send_message(
                            executed_function_responses_for_gemini,
                        )
                        text_from_gemini_for_discord_parts = []
                        if response_after_functions.candidates and response_after_functions.candidates[0].content:
                            final_model_content = response_after_functions.candidates[0].content
                            if final_model_content.role != "model":
                                final_model_content = types.Content(role="model", parts=final_model_content.parts)
                            current_session_history_entries.append(
                                HistoryEntry(timestamp=datetime.now(timezone.utc), content=final_model_content)
                            )
                            for part in final_model_content.parts:
                                if part.text:
                                    text_from_gemini_for_discord_parts.append(part.text)
                                elif part.function_call:
                                    logger.warning(f"⚠️ Model attempted a function call ({part.function_call.name}) after processing initial function results. This subsequent call will be handled if it's 'speak_message' or ignored.")
                                    if part.function_call.name == "speak_message":
                                        speak_args = dict(part.function_call.args) if part.function_call.args else {}
                                        speak_text = speak_args.get("text_to_speak")
                                        speak_style = speak_args.get("style")
                                        if speak_text:
                                            speak_tts_prompt = f"In a {speak_style.replace('_', ' ').lower()} tone, say: {speak_text}" if speak_style else speak_text
                                            speak_tts_result = await TTSGenerator.generate_speech_ogg(speak_tts_prompt)
                                            if speak_tts_result:
                                                ogg_audio_data, audio_duration, audio_waveform_b64 = speak_tts_result
                                                logger.info("🎤 TTS successful for follow-up 'speak_message'.")
                                                current_session_history_entries.append(HistoryEntry(timestamp=datetime.now(timezone.utc), content=types.Content(role="tool", parts=[part, types.Part(text="[TTS Executed as follow-up]") ])))
                                            else:
                                                logger.warning(f"🎤 TTS failed for follow-up 'speak_message'. Text: '{speak_text}'")
                                                text_from_gemini_for_discord_parts.insert(0, f"[Notice: Follow-up TTS failed for: \"{speak_text}\"]")
                                    else:
                                         logger.warning(f"🧠 Model attempted non-speak function call ({part.function_call.name}) after function results. Ignoring.")
                        else:
                            logger.error("❌ No valid content in Gemini's response after function execution (Main Call).")
                            text_from_gemini_for_discord_parts.append("[Error: AI did not provide a response after processing function results.]")
                final_text_for_discord = "\n".join(text_from_gemini_for_discord_parts).strip()
                if not final_text_for_discord and not ogg_audio_data:
                    final_text_for_discord = "I processed your request but have no further text to add."
                await chat_history_manager.save_history(guild_id_for_history, user_id_for_dm_history, current_session_history_entries)
                new_or_edited_bot_message = await MessageSender.send(
                    message, final_text_for_discord if final_text_for_discord else None,
                    ogg_audio_data, audio_duration, audio_waveform_b64, bot_message_to_edit
                )
                if new_or_edited_bot_message: active_bot_responses[message.id] = new_or_edited_bot_message
                else: active_bot_responses.pop(message.id, None)
            except types.StopCandidateException as sce:
                logger.error(f"❌ Gemini API StopCandidateException: {sce}", exc_info=True)
                error_reply_msg = await MessageSender.send(message, f"❌ The AI stopped responding unexpectedly (Reason: {sce.finish_reason}). Please try again.", None, existing_bot_message_to_edit=bot_message_to_edit)
                if error_reply_msg: active_bot_responses[message.id] = error_reply_msg
            except types.BlockedPromptException as bpe:
                logger.error(f"❌ Gemini API BlockedPromptException: {bpe}", exc_info=True)
                error_reply_msg = await MessageSender.send(message, "❌ Your request was blocked by the AI's safety filters. Please rephrase your request.", None, existing_bot_message_to_edit=bot_message_to_edit)
                if error_reply_msg: active_bot_responses[message.id] = error_reply_msg
            except Exception as e:
                logger.error(f"❌ Message processing pipeline error.\nUser: {message.author.name}\nError:\n{e}", exc_info=True)
                error_reply_msg = await MessageSender.send(message, "❌ I encountered an error processing your request.", None, existing_bot_message_to_edit=bot_message_to_edit)
                if error_reply_msg: active_bot_responses[message.id] = error_reply_msg
                else: active_bot_responses.pop(message.id, None)
@bot.event
async def on_ready():
    logger.info(f"🎉 Logged in as {bot.user.name} (ID: {bot.user.id})")
    logger.info(f"🔗 Discord.py Version: {discord.__version__}")
    logger.info(f"🧠 Using Main Gemini Model: {Config.MODEL_ID}")
    logger.info(f"🎤 Using TTS Gemini Model: {Config.MODEL_ID_TTS} with Voice: {Config.VOICE_NAME}")
    logger.info(f"💾 Chat History Max Turns (User+Assistant pairs): {Config.MAX_HISTORY_TURNS}")
    if Config.MAX_HISTORY_AGE > 0:
        logger.info(f"💾 Chat History Max Age (Minutes): {Config.MAX_HISTORY_AGE}")
    else:
        logger.info(f"💾 Chat History Max Age: Disabled")
    logger.info(f"🧠 User Memory Max Entries: {Config.MAX_MEMORIES}")
    try:
        activity_name = f"messages | {bot.command_prefix}reset | {bot.command_prefix}forget"
        await bot.change_presence(activity=discord.Activity(type=discord.ActivityType.listening, name=activity_name))
    except Exception as e:
        logger.warning(f"⚠️ Could not set bot presence.\nError:\n{e}")
@bot.event
async def on_message(message: discord.Message):
    if message.author == bot.user or message.author.bot: return
    is_dm = isinstance(message.channel, discord.DMChannel)
    is_mentioned = bot.user.mentioned_in(message) if bot.user else False
    is_reply_to_bot = False
    if message.reference and message.reference.message_id:
        try:
            if hasattr(message.channel, 'fetch_message'):
                referenced_message = await message.channel.fetch_message(message.reference.message_id)
                if referenced_message.author == bot.user:
                    is_reply_to_bot = True
            elif isinstance(message.channel, discord.DMChannel) and message.reference.cached_message:
                 if message.reference.cached_message.author == bot.user:
                    is_reply_to_bot = True
        except (discord.NotFound, discord.Forbidden, discord.HTTPException):
            pass
    content_lower = message.content.lower().strip()
    is_reset_command = content_lower.startswith(f"{bot.command_prefix}reset")
    is_forget_command = content_lower.startswith(f"{bot.command_prefix}forget")
    if is_dm or is_mentioned or is_reply_to_bot or is_reset_command or is_forget_command:
        if message.id in active_bot_responses and bot_message_to_edit is None:
             logger.warning(f"⚠️ Message {message.id} is already associated with an active response. Skipping new on_message event.")
             return
        await MessageProcessor.process(message)
@bot.event
async def on_message_edit(before: discord.Message, after: discord.Message):
    if after.author == bot.user or after.author.bot:
        return
    if before.content == after.content and \
       (not before.attachments and not after.attachments or before.attachments == after.attachments) and \
       (not before.embeds and after.embeds and not any(e.type == 'gifv' for e in after.embeds)):
        logger.info(f"ℹ️ Message edit ignored for Message ID {after.id}: likely initial embed generation by Discord (non-gifv).")
        return
    is_dm_after = isinstance(after.channel, discord.DMChannel)
    is_mentioned_after = bot.user.mentioned_in(after) if bot.user else False
    is_reply_to_bot_after = False
    if after.reference and after.reference.message_id:
        try:
            if hasattr(after.channel, 'fetch_message'):
                referenced_message_after = await after.channel.fetch_message(after.reference.message_id)
                if referenced_message_after.author == bot.user:
                    is_reply_to_bot_after = True
            elif isinstance(after.channel, discord.DMChannel) and after.reference.cached_message:
                if after.reference.cached_message.author == bot.user:
                    is_reply_to_bot_after = True
        except (discord.NotFound, discord.Forbidden, discord.HTTPException) as e:
            logger.debug(f"🔍 Could not fetch referenced message for edit qualification check (Msg ID: {after.id}, Ref ID: {after.reference.message_id}). Error: {e}")
    content_lower_after = after.content.lower().strip()
    is_reset_command_after = content_lower_after.startswith(f"{bot.command_prefix}reset")
    is_forget_command_after = content_lower_after.startswith(f"{bot.command_prefix}forget")
    should_process_after = is_dm_after or is_mentioned_after or is_reply_to_bot_after or is_reset_command_after or is_forget_command_after
    existing_bot_response = active_bot_responses.get(after.id)
    if not should_process_after:
        if existing_bot_response:
            logger.info(f"🗑️ Edited message (ID: {after.id}) no longer qualifies for bot processing. Deleting previous bot response (ID: {existing_bot_response.id}).")
            try:
                await existing_bot_response.delete()
            except discord.HTTPException as e:
                logger.warning(f"⚠️ Could not delete previous bot response (ID: {existing_bot_response.id}) for edited message that no longer qualifies. Error: {e}")
            finally:
                active_bot_responses.pop(after.id, None)
        return
    if before.content == after.content and \
       (not before.attachments and not after.attachments or before.attachments == after.attachments) and \
       (not before.embeds and after.embeds and not any(e.type == 'gifv' for e in after.embeds)):
        logger.info(f"ℹ️ Edit on qualifying message (ID: {after.id}) ignored: likely initial embed generation by Discord. No reprocessing needed.")
        return
    logger.info(f"📥 Edited message (ID: {after.id}) qualifies for processing and is a substantive edit. Reprocessing.")
    await MessageProcessor.process(after, bot_message_to_edit=existing_bot_response)
@bot.event
async def on_message_delete(message: discord.Message):
    if message.id in active_bot_responses:
        bot_response_to_delete = active_bot_responses.pop(message.id, None)
        if bot_response_to_delete:
            try:
                await bot_response_to_delete.delete()
                logger.info(f"🗑️ Bot response deleted because original user message was deleted.\nBot Response ID: {bot_response_to_delete.id}\nUser Message ID: {message.id}")
            except discord.HTTPException: pass
def validate_environment_variables():
    """Validates that essential environment variables are set."""
    if not Config.DISCORD_BOT_TOKEN:
        raise ValueError("DISCORD_BOT_TOKEN not found in environment variables.")
    if not Config.GEMINI_API_KEY:
        raise ValueError("GEMINI_API_KEY not found in environment variables.")
    logger.info("✅ Environment variables validated.")
def setup_logging():
    """Configures logging for the application."""
    for handler in logging.root.handlers[:]:
        logging.root.removeHandler(handler)
    logging.basicConfig(level=logging.WARNING, format='%(asctime)s [%(levelname)s] [%(name)s] %(message)s', handlers=[logging.StreamHandler()], force=True)
    app_logger = logging.getLogger("Bard")
    for handler in app_logger.handlers[:]:
        app_logger.removeHandler(handler)
    app_logger.setLevel(logging.INFO)
    app_logger.propagate = False
    console_handler = logging.StreamHandler()
    console_formatter = logging.Formatter('%(message)s')
    console_handler.setFormatter(console_formatter)
    app_logger.addHandler(console_handler)
    try:
        file_handler = logging.FileHandler('.log', mode='a', encoding='utf-8')
        detailed_file_formatter = logging.Formatter('%(asctime)s [%(levelname)s] [%(name)s:%(module)s:%(funcName)s:%(lineno)d] %(message)s')
        file_handler.setFormatter(detailed_file_formatter)
        app_logger.addHandler(file_handler)
    except Exception as e:
        app_logger.error(f"Failed to set up file logging: {e}")
    logging.getLogger("discord").setLevel(logging.WARNING)
    logging.getLogger("discord.http").setLevel(logging.WARNING)
    logging.getLogger("google.generativeai").setLevel(logging.WARNING)
    logging.getLogger("httpx").setLevel(logging.WARNING)
    logging.getLogger("httpcore").setLevel(logging.WARNING)
    app_logger.info("⚙️ Logging configured.")
def main():
        global gemini_client, chat_history_manager, memory_manager
        try:
            setup_logging()
            logger.info("🚀 Initializing Gemini Discord Bot...")
            validate_environment_variables()
            gemini_client = genai.Client(api_key=Config.GEMINI_API_KEY, http_options={'api_version': 'v1beta'})
            chat_history_manager = ChatHistoryManager()
            memory_manager = MemoryManager()
            logger.info(f"🤖 Gemini AI Client (genai.Client) initialized. Target API version: v1beta")
            logger.info(f"💾 Chat History Manager initialized.")
            logger.info(f"🧠 Memory Manager initialized.")
            logger.info("📡 Starting Discord bot...")
            bot.run(Config.DISCORD_BOT_TOKEN, log_handler=None)
        except ValueError as ve:
            print(f"Configuration Error: {ve}")
            if logger and logger.handlers: logger.critical(f"💥 Configuration Error:\n{ve}", exc_info=True)
            else: print(f"CRITICAL: Configuration Error: {ve}")
            return 1
        except discord.LoginFailure as lf:
            log_msg = f"🛑 Discord Login Failed. Check bot token and intents.\nError:\n{lf}"
            print(log_msg)
            if logger and logger.handlers: logger.critical(log_msg)
            else: print(f"CRITICAL: {log_msg}")
            return 1
        except Exception as e:
            log_msg = f"💥 Fatal error during bot execution:\n{e}"
            print(log_msg)
            if logger and logger.handlers: logger.critical(log_msg, exc_info=True)
            else: print(f"CRITICAL: {log_msg} - {e}")
            return 1
        finally:
            if logger and logger.handlers: logger.info("🛑 Bot shutdown sequence initiated.")
            else: print("INFO: Bot shutdown sequence initiated.")
        return 0
if __name__ == "__main__":
    exit_code = main()
    final_log_msg_base = "Bot exited"
    if exit_code == 0:
        final_log_msg = f"✅ {final_log_msg_base} gracefully."
    else:
        final_log_msg = f"⚠️ {final_log_msg_base} with error code: {exit_code}."
    if logger and logger.handlers:
        if exit_code == 0: logger.info(final_log_msg)
        else: logger.warning(final_log_msg)
        logging.shutdown()
    else:
        print(final_log_msg)
