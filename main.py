import asyncio
import base64
import io
import logging
import os
import re
import tempfile
import wave
import json
from datetime import datetime
from collections import defaultdict

import aiohttp
import discord
import numpy as np
import soundfile
from discord.ext import commands
from dotenv import load_dotenv
from google import genai
from google.genai import types
from google.genai.chats import Chat as GenAIChatSession

# Application-specific logger
logger = logging.getLogger("Bard")

# --- Configuration ---
class Config:
    """Stores all configuration constants for the bot."""
    MODEL_ID = "gemini-2.5-flash-preview-05-20"
    TTS_MODEL_ID = "gemini-2.5-flash-preview-tts" # Specific model for TTS
    VOICE_NAME = "Kore"  # Prebuilt voice for TTS

    MAX_MESSAGE_LENGTH = 2000  # Discord message length limit
    MAX_REPLY_DEPTH = 10       # Max depth for fetching reply chains
    THINKING_BUDGET = 1024     # Token budget for Gemini's thinking process
    MAX_OUTPUT_TOKENS = 2048   # Max tokens for Gemini's response

    # TTS Audio Properties (matching Gemini TTS output)
    TTS_SAMPLE_RATE = 24000    # Hz
    TTS_CHANNELS = 1           # Mono
    TTS_SAMPLE_WIDTH = 2       # Bytes per sample (16-bit PCM)

    # Fallback waveform for Discord voice messages if generation fails
    DEFAULT_WAVEFORM_PLACEHOLDER = "FzYACgAAAAAAACQAAAAAAAA=" # Default placeholder

    # FFMPEG path (can be overridden by environment variable)
    FFMPEG_PATH = os.getenv("FFMPEG_PATH", "ffmpeg")

    # Prompt and History Configuration
    PROMPT_DIR = "prompts" # Directory to load .prompt.md files from
    HISTORY_DIR = "history" # Directory to save and load .history.json files
    MAX_HISTORY_TURNS = 16  # Number of user + assistant turn pairs (e.g., 16 turns = 32 content entries)


# --- Environment Setup ---
load_dotenv()
DISCORD_BOT_TOKEN = os.getenv("DISCORD_BOT_TOKEN")
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY")

# --- Global State ---
active_bot_responses = {} # Stores user_message_id -> bot_response_message_object
gemini_client = None # Initialized in main()
chat_history_manager = None # Initialized in main()

# --- Discord Bot Setup ---
intents = discord.Intents.default()
intents.message_content = True  # Required for reading message content
bot = commands.Bot(command_prefix="!", intents=intents)


# --- Prompt Management ---
class PromptManager:
    @staticmethod
    def load_combined_system_prompt() -> str:
        """Loads and combines all .prompt.md files from the Config.PROMPT_DIR."""
        prompt_contents = []
        prompt_dir = Config.PROMPT_DIR

        if not os.path.isdir(prompt_dir):
            logger.error(f"❌ Prompt directory not found. Using fallback system prompt.\nDirectory:\n{prompt_dir}")
            # Fallback prompt remains the same as before
            return (
                "You are a helpful AI assistant on Discord. Be concise and helpful. "
            )

        # Ensure a consistent order of loading prompts by sorting filenames
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

        # Combine all loaded prompt sections. Using a double newline as a separator.

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

        if message.guild: # Not a DM
            guild_name_str = f"{message.guild.name} (ID: {message.guild.id})"
            if isinstance(channel, discord.Thread):
                channel_name_str = f"{channel.parent.name}/{channel.name} (ID: {channel.id})"
            elif hasattr(channel, 'name'):
                channel_name_str = f"{channel.name} (ID: {channel.id})"
            else: # Fallback if channel name attribute is missing
                channel_name_str = f"Unknown Channel (ID: {channel.id})"
        else: # DM
            channel_name_str = f"Direct Message with {user.display_name} (Channel ID: {channel.id})"

        metadata_content = f"""[DYNAMIC_CONTEXT:START]
Timestamp: {datetime.now().strftime('%Y-%m-%d %H:%M:%S UTC')}
Guild: {guild_name_str}
Channel: {channel_name_str}
User: {user.display_name} (@{user.name}, ID: {user.id})
User Mention: <@{user.id}>
[DYNAMIC_CONTEXT:END]"""
        return metadata_content

# --- Chat History Management ---
class ChatHistoryManager:
    def __init__(self):
        self.locks = defaultdict(asyncio.Lock)
        try:
            os.makedirs(Config.HISTORY_DIR, exist_ok=True)
            logger.info(f"💾 Chat history directory:{Config.HISTORY_DIR}")
        except OSError as e:
            logger.error(f"❌ Could not create chat history directory.\nDirectory:\n{Config.HISTORY_DIR}\nError:\n{e}", exc_info=True)
            # Depending on desired behavior, might raise an exception or try to proceed without persistence.
            # For now, log and proceed, loading/saving will likely fail gracefully.

    def _get_history_filepath(self, guild_id: int | None, user_id: int | None = None) -> str:
        """
        Constructs the file path for chat history.
        For guild-level history, user_id is ignored.
        For DM history, guild_id is None, and user_id is used.
        """
        if guild_id is not None: # Guild-level history
            filename = f"{guild_id}.history.json"
        elif user_id is not None: # DM history
            filename = f"DM_{user_id}.history.json"
        else:
            # This case should ideally not be reached if logic is correct
            logger.error("❌ Attempted to get history filepath with neither guild_id nor user_id.")
            filename = "unknown_history.history.json"
        return os.path.join(Config.HISTORY_DIR, filename)

    async def load_history(self, guild_id: int | None, user_id: int | None = None) -> list[types.Content]:
        filepath = self._get_history_filepath(guild_id, user_id)
        history_list = []

        async with self.locks[filepath]:
            if not os.path.exists(filepath):
                logger.info(f"💾 No history file found. Starting fresh.\nFilepath: {filepath}")
                return []
            try:
                with open(filepath, "r", encoding="utf-8") as f:
                    raw_history = json.load(f)
                    reconstructed_history = []
                    for item_dict in raw_history:
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
                                    # Optionally, add a placeholder part or skip
                                    loaded_parts.append(types.Part(text="[Error: Could not load inline_data from history]"))
                            elif "file_data" in part_dict:
                                loaded_parts.append(types.Part(file_data=types.FileData(
                                    mime_type=part_dict["file_data"]["mime_type"],
                                    file_uri=part_dict["file_data"]["file_uri"]
                                )))
                            # Add handling for other part types if necessary

                        # Ensure role is valid, default to "user" or "model" if missing/invalid
                        role = item_dict.get("role", "user")
                        if role not in ("user", "model"):
                            logger.warning(f"⚠️ Invalid role found in history file. Defaulting to 'user'.\nRole:\n{role}")
                            role = "user" # Or handle as an error

                        reconstructed_history.append(types.Content(role=role, parts=loaded_parts))
                    history_list = reconstructed_history
                logger.info(f"💾 Loaded {len(history_list)} history entries from {filepath}.")
            except json.JSONDecodeError:
                logger.error(f"❌ Could not decode JSON from history file. Starting with fresh history for this session.\nFilepath: {filepath}")
                # Optionally, backup the corrupted file here
                return []
            except Exception as e:
                logger.error(f"❌ Error loading history from file.\nFilepath: {filepath}\nError:\n{e}", exc_info=True)
                return []

        # Truncate to MAX_HISTORY_TURNS (each turn has a user and a model part)
        max_entries = Config.MAX_HISTORY_TURNS * 2
        if len(history_list) > max_entries:
            history_list = history_list[-max_entries:]
            logger.info(f"💾 History truncated.\nEntries: {len(history_list)}\nMax Entries: {max_entries}")

        return history_list

    async def save_history(self, guild_id: int | None, user_id: int | None, history: list[types.Content]):
        filepath = self._get_history_filepath(guild_id, user_id)

        # Truncate before saving
        max_entries = Config.MAX_HISTORY_TURNS * 2
        if len(history) > max_entries:
            history_to_save = history[-max_entries:]
        else:
            history_to_save = history

        logger.info(f"💾 Saving {len(history_to_save)} history entries to file.")

        # Convert types.Content objects to serializable dictionaries
        serializable_history = []
        for content_item in history_to_save:
            parts_list = []
            for part in content_item.parts:
                if part.text is not None:
                    parts_list.append({"text": part.text})
                elif part.inline_data is not None:
                    # For simplicity, store inline_data's mime_type and base64 encoded data

                    parts_list.append({
                        "inline_data": {
                            "mime_type": part.inline_data.mime_type,
                            "data": base64.b64encode(part.inline_data.data).decode('utf-8') # Store data as b64 string
                        }
                    })
                elif part.file_data is not None:
                    parts_list.append({
                        "file_data": {
                            "mime_type": part.file_data.mime_type,
                            "file_uri": part.file_data.file_uri
                        }
                    })
                # Add other part types if you use them (e.g., function_call, function_response)
            serializable_history.append({
                "role": content_item.role,
                "parts": parts_list
            })

        temp_filepath = filepath + ".tmp"
        async with self.locks[filepath]:
            try:
                with open(temp_filepath, "w", encoding="utf-8") as f:
                    json.dump(serializable_history, f, indent=2)
                os.replace(temp_filepath, filepath) # Atomic rename
                logger.info(f"💾 History successfully saved to {filepath}.")
            except Exception as e:
                logger.error(f"❌ Error saving history to file.\nFilepath: {filepath}\nError:\n{e}", exc_info=True)
                if os.path.exists(temp_filepath):
                    try:
                        os.remove(temp_filepath)
                    except OSError as e_rem:
                        logger.warning(f"⚠️ Could not remove temporary history file.\nFilepath: {temp_filepath}\nError:\n{e_rem}")

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

# --- Utility Classes (MimeDetector, YouTubeProcessor, TTSGenerator, MessageSender, AttachmentProcessor, ReplyChainProcessor) ---




class MimeDetector:
    """
    Detects MIME types from byte data using known signatures.
    Note: This is a basic detector. For more comprehensive detection,
    consider integrating with libraries like `python-magic` or using
    the `mimetypes` module as a first pass.
    """
    MIME_SIGNATURES = {
        # Images
        b'\x89PNG': 'image/png',
        b'\xff\xd8\xff': 'image/jpeg',
        b'GIF8': 'image/gif',
        # WebP is handled by RIFF check
        # Audio
        b'ID3': 'audio/mpeg',      # MP3
        b'\xff\xfb': 'audio/mpeg',  # MP3 (alternative signature)
        # WAV is handled by RIFF check
        # Documents
        b'%PDF': 'application/pdf',
        # Other common types can be added here
    }

    @classmethod
    def detect(cls, data: bytes) -> str:
        """
        Detects the MIME type of the given byte data.

        Args:
            data: The byte data to inspect.

        Returns:
            The detected MIME type string, or 'application/octet-stream' if unknown.
        """
        if data.startswith(b'RIFF'):
            # RIFF is a container format, check for specific types within it
            if b'WEBP' in data[8:12]:  # e.g., WEBPVP8 for WebP
                return 'image/webp'
            elif b'WAVE' in data[8:12]: # e.g., WAVEfmt for WAV
                return 'audio/wav'
            logger.debug("🔍 Detected RIFF container, but not specifically WEBP or WAV. Falling back.")
            # Fallback for generic RIFF if not WebP/WAV, though less common for uploads
            return 'application/vnd.rn-realmedia'

        for signature, mime_type in cls.MIME_SIGNATURES.items():
            if data.startswith(signature):
                return mime_type

        # Check for MP4-based containers (common for video/audio)
        # These often have 'ftyp' or 'moov' near the beginning after the size atom.
        if b'ftyp' in data[4:8] or b'moov' in data[4:8] or \
           (len(data) > 8 and b'ftyp' in data[4:12]) or \
           (len(data) > 8 and b'moov' in data[4:12]): # More robust check for ftyp
            logger.debug("🔍 Detected MP4-based container (ftyp/moov). Assuming 'video/mp4'.")
            return 'video/mp4'


        logger.debug("🔍 MIME type not identified by known signatures. Defaulting to 'application/octet-stream'.")
        return 'application/octet-stream' # Default fallback

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
                return duration_secs, Config.DEFAULT_WAVEFORM_PLACEHOLDER

            if np.issubdtype(mono_audio_data.dtype, np.integer):
                 mono_audio_data = mono_audio_data / np.iinfo(mono_audio_data.dtype).max

            step = max(1, num_samples // max_waveform_points)
            waveform_raw_bytes = bytearray()
            for i in range(0, num_samples, step):
                chunk = mono_audio_data[i:i+step]
                if len(chunk) == 0: continue
                rms = np.sqrt(np.mean(chunk**2))
                scaled_value = int(min(rms * 5.0, 1.0) * 255) # Scale factor 5.0 for visualization
                waveform_raw_bytes.append(scaled_value)

            if not waveform_raw_bytes:
                return duration_secs, Config.DEFAULT_WAVEFORM_PLACEHOLDER

            waveform_b64 = base64.b64encode(waveform_raw_bytes).decode('utf-8')
            return duration_secs, waveform_b64
        except Exception as e:
            logger.error(f"❌ Error getting duration/waveform for audio file.\nFile:\n{audio_path}\nError:\n{e}", exc_info=True)
            try:
                # Fallback to get duration if waveform generation failed
                info = soundfile.info(audio_path)
                return info.duration, Config.DEFAULT_WAVEFORM_PLACEHOLDER
            except Exception as e_info:
                logger.error(f"❌ Fallback to get duration also failed for audio file.\nFile:\n{audio_path}\nError:\n{e_info}", exc_info=True)
                return 1.0, Config.DEFAULT_WAVEFORM_PLACEHOLDER # Absolute fallback

    @staticmethod
    async def generate_speech_ogg(text_for_tts: str) -> tuple[bytes, float, str] | None:
        """Generates speech audio in OGG Opus format from text using Gemini TTS."""
        global gemini_client
        if not gemini_client:
            logger.error("❌ Gemini client not initialized. Cannot generate TTS.")
            return None

        tmp_wav_path, tmp_ogg_path = None, None
        try:
            logger.info(f"🎤 Generating TTS (WAV) with details:\nText:\n{text_for_tts}\nVoice: {Config.VOICE_NAME}\nModel: {Config.TTS_MODEL_ID}")
            speech_generation_config = types.GenerateContentConfig(
                response_modalities=["AUDIO"],
                speech_config=types.SpeechConfig(
                    voice_config=types.VoiceConfig(prebuilt_voice_config=types.PrebuiltVoiceConfig(voice_name=Config.VOICE_NAME))
                )
            )
            response = await gemini_client.aio.models.generate_content(
                model=Config.TTS_MODEL_ID, contents=text_for_tts, config=speech_generation_config
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
            text_content = "I processed your request but have no further text to add." # Default text if model provides none

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
            # Split by paragraphs to maintain some formatting if possible
            for paragraph in remaining_text.split('\n\n'):
                if len(current_chunk + paragraph + '\n\n') > Config.MAX_MESSAGE_LENGTH:
                    if current_chunk.strip():
                        try: await message_to_reply_to.channel.send(current_chunk.strip())
                        except discord.HTTPException as e: logger.error(f"❌ Failed to send subsequent message chunk.\nError:\n{e}", exc_info=True)
                    current_chunk = paragraph + '\n\n'
                else:
                    current_chunk += paragraph + '\n\n'
            if current_chunk.strip(): # Send the last remaining chunk
                try: await message_to_reply_to.channel.send(current_chunk.strip())
                except discord.HTTPException as e: logger.error(f"❌ Failed to send final message chunk.\nError:\n{e}", exc_info=True)
        else: # Message is within the length limit
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
                     waveform_b64: str = Config.DEFAULT_WAVEFORM_PLACEHOLDER,
                     existing_bot_message_to_edit: discord.Message | None = None) -> discord.Message | None:
        """Sends a reply to a Discord message. Can be text, voice, or both."""
        # Determine if we can attempt to send as a native Discord voice message
        # Conditions: audio_data exists, bot token is available, and there's no primary text content to display.
        can_try_native_voice = audio_data and DISCORD_BOT_TOKEN and (not text_content or not text_content.strip())
        temp_ogg_file_path_for_upload = None # Path for OGG file if native voice message is attempted

        if existing_bot_message_to_edit:
            # If only text content is provided for an existing message, try to edit it.
            if text_content and not audio_data:
                try:
                    # Check if the existing message is a simple text message (no attachments, not a voice message itself)
                    is_simple_text_message = not existing_bot_message_to_edit.attachments and \
                                             not (existing_bot_message_to_edit.flags and existing_bot_message_to_edit.flags.value & 8192) # 8192 is voice message flag
                    if is_simple_text_message:
                        await existing_bot_message_to_edit.edit(content=text_content[:Config.MAX_MESSAGE_LENGTH])
                        logger.info(f"✏️ Edited existing bot message with text.\nID: {existing_bot_message_to_edit.id}")
                        return existing_bot_message_to_edit
                except discord.HTTPException as e:

                    logger.error(f"❌ Failed to edit bot message with text. Falling back to delete and resend.\nID: {existing_bot_message_to_edit.id}\nError:\n{e}", exc_info=True)
                except Exception as e_unhandled: # Catch any other unexpected errors during edit
                    logger.error(f"❌ Unhandled error editing bot message. Falling back to delete and resend.\nID: {existing_bot_message_to_edit.id}\nError:\n{e_unhandled}", exc_info=True)

                # Fallback: If editing failed or wasn't suitable, delete the old message to resend a new one.
                try:
                    await existing_bot_message_to_edit.delete()
                except discord.HTTPException: pass # Ignore if already deleted or no permissions
            else: # If the new response type involves audio or is otherwise different, delete the old message.
                try: await existing_bot_message_to_edit.delete()
                except discord.HTTPException: pass


        if can_try_native_voice:
            channel_id = str(message_to_reply_to.channel.id)
            try:
                # Create a temporary file for the OGG audio data
                with tempfile.NamedTemporaryFile(suffix='.ogg', delete=False) as temp_audio_file:
                    temp_audio_file.write(audio_data)
                    temp_ogg_file_path_for_upload = temp_audio_file.name

                async with aiohttp.ClientSession() as session:
                    # 1. Request an upload slot from Discord
                    upload_slot_api_url = f"https://discord.com/api/v10/channels/{channel_id}/attachments"
                    upload_slot_payload = {"files": [{"filename": "voice_message.ogg", "file_size": len(audio_data), "id": "0", "is_clip": False}]}
                    upload_slot_headers = {"Authorization": f"Bot {DISCORD_BOT_TOKEN}", "Content-Type": "application/json"}

                    attachment_metadata = None
                    async with session.post(upload_slot_api_url, json=upload_slot_payload, headers=upload_slot_headers) as resp_slot:
                        if resp_slot.status == 200:
                            resp_slot_json = await resp_slot.json()
                            if resp_slot_json.get("attachments") and len(resp_slot_json["attachments"]) > 0:
                                attachment_metadata = resp_slot_json["attachments"][0]
                            else:
                                response_text = await resp_slot.text()
                                raise Exception(f"Invalid attachment slot response from Discord API.\nResponse:\n{response_text}")
                        else:
                            response_text = await resp_slot.text()
                            raise Exception(f"Failed to get Discord upload slot.\nStatus: {resp_slot.status}\nResponse:\n{response_text}")

                    # 2. Upload the audio file to the obtained Discord CDN URL
                    put_url = attachment_metadata["upload_url"]
                    with open(temp_ogg_file_path_for_upload, 'rb') as file_to_put:
                        async with session.put(put_url, data=file_to_put, headers={'Content-Type': 'audio/ogg'}) as resp_put:
                            if resp_put.status != 200:
                                response_text = await resp_put.text()
                                raise Exception(f"Failed to PUT audio to Discord CDN.\nStatus: {resp_put.status}\nResponse:\n{response_text}")

                    # 3. Send the message with attachment metadata pointing to the uploaded file
                    discord_cdn_filename = attachment_metadata["upload_filename"]
                    send_message_api_url = f"https://discord.com/api/v10/channels/{channel_id}/messages"
                    send_message_payload = {
                        "content": "", "flags": 8192, # Flag 8192 indicates a voice message
                        "attachments": [{"id": "0", "filename": "voice_message.ogg", "uploaded_filename": discord_cdn_filename,
                                         "duration_secs": round(duration_secs, 2), "waveform": waveform_b64}],
                        "message_reference": {"message_id": str(message_to_reply_to.id)},
                        "allowed_mentions": {"parse": [], "replied_user": False} # Control pings
                    }
                    send_message_headers = {"Authorization": f"Bot {DISCORD_BOT_TOKEN}", "Content-Type": "application/json"}

                    async with session.post(send_message_api_url, json=send_message_payload, headers=send_message_headers) as resp_send:
                        if resp_send.status == 200 or resp_send.status == 201: # 201 Created can also occur
                            response_data = await resp_send.json()
                            message_id = response_data.get("id")
                            if message_id:
                                try:
                                    # Fetch the message object to return it, ensuring it's a full discord.Message
                                    sent_message = await message_to_reply_to.channel.fetch_message(message_id)
                                    logger.info(f"🎤 Sent native Discord voice message.\nID: {sent_message.id}\nTo: {message_to_reply_to.author.name}\nIn Channel: #{message_to_reply_to.channel}")
                                    return sent_message
                                except discord.HTTPException:
                                    logger.warning("🎤 Sent native voice message, but failed to fetch the discord.Message object afterwards.")
                                    pass # Message was sent, but we couldn't get the object.
                            return None # Return None if ID wasn't found or fetch failed
                        else:
                            response_text = await resp_send.text()
                            raise Exception(f"Discord API send voice message failed.\nStatus: {resp_send.status}\nResponse:\n{response_text}")
            except Exception as e:
                logger.error(f"❌ Error sending native Discord voice message. Falling back to file upload or text.\nError:\n{e}", exc_info=True)
                # Fallback logic for native voice failure:
                # If audio data exists (and we have the temp file path), try sending as a regular file attachment.
                if temp_ogg_file_path_for_upload and os.path.exists(temp_ogg_file_path_for_upload):
                    try:
                        discord_file = discord.File(temp_ogg_file_path_for_upload, "voice_response.ogg")
                        fallback_msg = await message_to_reply_to.reply(file=discord_file)
                        logger.info(f"📎 Sent voice response as .ogg file attachment (fallback).\nID: {fallback_msg.id}")
                        # If there was suppressed text_content because native voice was being attempted, send it now.
                        if text_content and text_content.strip():
                             await MessageSender._send_text_reply(message_to_reply_to, text_content) # This will log separately
                        return fallback_msg
                    except Exception as fallback_e:
                        logger.error(f"❌ Fallback .ogg file send also failed.\nError:\n{fallback_e}", exc_info=True)
            finally:
                # Clean up the temporary OGG file if it was created
                if temp_ogg_file_path_for_upload and os.path.exists(temp_ogg_file_path_for_upload):
                    try: os.unlink(temp_ogg_file_path_for_upload)
                    except OSError: pass # Ignore error on cleanup, not critical

            # If native voice path was taken, failed, and there's still text_content that needs sending (e.g., if fallback file send also failed)
            if text_content and text_content.strip():
                return await MessageSender._send_text_reply(message_to_reply_to, text_content)
            return None # If all attempts fail after trying native voice


        # Standard path: Text message and/or separate audio file attachment (not native voice message)
        sent_text_message = None
        if text_content and text_content.strip():
            sent_text_message = await MessageSender._send_text_reply(message_to_reply_to, text_content) # This logs internally

        sent_audio_file_message = None
        if audio_data and not can_try_native_voice: # e.g., text was also present, or native voice conditions not met
            temp_ogg_path_regular = None
            try:
                with tempfile.NamedTemporaryFile(suffix='.ogg', delete=False) as temp_audio_file:
                    temp_audio_file.write(audio_data)
                    temp_ogg_path_regular = temp_audio_file.name
                discord_file = discord.File(temp_ogg_path_regular, "voice_response.ogg")
                # If text was already sent as a reply, send audio as a new message in channel. Otherwise, reply with audio.
                if sent_text_message:
                    sent_audio_file_message = await message_to_reply_to.channel.send(file=discord_file)
                else:
                    sent_audio_file_message = await message_to_reply_to.reply(file=discord_file)
                if sent_audio_file_message:
                    logger.info(f"📎 Sent voice response as .ogg file attachment.\nID: {sent_audio_file_message.id}")
            except Exception as e:
                logger.error(f"❌ Failed to send .ogg file as attachment.\nError:\n{e}", exc_info=True)
            finally:
                if temp_ogg_path_regular and os.path.exists(temp_ogg_path_regular):
                    try: os.unlink(temp_ogg_path_regular)
                    except OSError: pass # Non-critical cleanup error

        # Return the primary message sent: text if available, otherwise the audio file message.
        return sent_text_message or sent_audio_file_message

class AttachmentProcessor:
    """Downloads Discord attachments and prepares them for Gemini."""
    @staticmethod
    async def _download_and_prepare_attachment(attachment: discord.Attachment) -> tuple[io.BytesIO, str, str] | None:
        try:
            async with aiohttp.ClientSession() as session:
                async with session.get(attachment.url) as response:
                    if response.status == 200:
                        data = await response.read()
                        mime_type = attachment.content_type
                        if not mime_type or mime_type == 'application/octet-stream': # If Discord doesn't provide a good one
                            mime_type = MimeDetector.detect(data) # Try our own detection
                        return io.BytesIO(data), mime_type, attachment.filename
                    else:
                        logger.warning(f"⚠️ Failed to download attachment.\nFilename: {attachment.filename}\nURL: {attachment.url}\nHTTP Status: {response.status}")
                        return None
        except Exception as e:
            logger.error(f"❌ Error downloading attachment.\nFilename: {attachment.filename}\nURL: {attachment.url}\nError:\n{e}", exc_info=True)
            return None

    @staticmethod
    async def _upload_to_file_api(file_like_object: io.BytesIO, mime_type: str, display_name: str) -> types.File | types.Part:
        global gemini_client
        if not gemini_client:
            logger.error("❌ Gemini client not initialized. Cannot upload file to File API.")
            return types.Part(text=f"[Attachment: {display_name} - Gemini client not ready for upload]")
        try:
            file_like_object.seek(0) # Ensure reading from the beginning of the stream
            uploaded_file = await gemini_client.aio.files.upload(
                file=file_like_object,
                config=types.UploadFileConfig(mime_type=mime_type, display_name=display_name)
            )
            logger.info(f"📎 Successfully uploaded file to Gemini File API.\nDisplay Name: {display_name}\nMIME Type: {mime_type}\nFile URI: {uploaded_file.uri}")
            return uploaded_file # This is a types.File object
        except Exception as e:
            logger.error(f"❌ Error uploading file to Gemini File API.\nDisplay Name: {display_name}\nMIME Type: {mime_type}\nError:\n{e}", exc_info=True)
            # Return a Part with error information for the LLM to potentially see
            return types.Part(text=f"[Attachment: {display_name} - Gemini File API Upload failed. Error: {str(e)}]")

    @staticmethod
    async def process_discord_attachments(attachments: list[discord.Attachment]) -> list[types.Part]:
        parts: list[types.Part] = []
        if not attachments: return parts

        for attachment in attachments:
            prepared_data = await AttachmentProcessor._download_and_prepare_attachment(attachment)
            if prepared_data:
                file_io, mime, fname = prepared_data
                upload_result = await AttachmentProcessor._upload_to_file_api(file_io, mime, fname)
                if isinstance(upload_result, types.File):
                    # Create a FileData Part using the URI from the uploaded file
                    parts.append(types.Part(file_data=types.FileData(mime_type=upload_result.mime_type, file_uri=upload_result.uri)))
                elif isinstance(upload_result, types.Part): # This means an error Part was returned from _upload_to_file_api
                    parts.append(upload_result)
            else: # Download or preparation failed
                parts.append(types.Part(text=f"[Attachment: {attachment.filename} - Download or preparation failed.]"))
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
                'message_obj': current_msg_obj, # Keep the object for potential future use
                'author_name': f"{current_msg_obj.author.display_name} (@{current_msg_obj.author.name})",
                'author_id': current_msg_obj.author.id,
                'is_bot': current_msg_obj.author.bot,
                'content': current_msg_obj.content, # Raw content
                'attachments': list(current_msg_obj.attachments), # List of discord.Attachment objects
            }
            chain.insert(0, msg_info) # Insert at the beginning to maintain chronological order
            if hasattr(current_msg_obj, 'reference') and current_msg_obj.reference and current_msg_obj.reference.message_id:
                try:
                    current_msg_obj = await current_msg_obj.channel.fetch_message(current_msg_obj.reference.message_id)
                    depth += 1
                except (discord.NotFound, discord.Forbidden, discord.HTTPException): # Stop if message not found or no perms
                    break
            else: # No more references
                break
        return chain

    @staticmethod
    def format_context_for_llm(chain: list[dict], current_message_id: int, bot_user_id: int) -> str:
        if len(chain) <= 1: return "" # No reply context if only the current message is in the "chain"

        context_str = "\n[REPLY_CONTEXT:START]\n"
        for msg_data in chain:
            # Skip formatting the current message itself into the reply context block
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
    def create_config(system_instruction_str: str) -> types.GenerateContentConfig:
        """Creates the Gemini generation configuration using a provided system instruction string."""
        config = types.GenerateContentConfig(
            system_instruction=system_instruction_str, # The main system prompt

            temperature=1.0,      # Controls randomness
            top_p=0.95,           # Nucleus sampling
            max_output_tokens=Config.MAX_OUTPUT_TOKENS,
            safety_settings=[     # Configure safety settings to be less restrictive
                types.SafetySetting(category=cat, threshold=types.HarmBlockThreshold.BLOCK_NONE)
                for cat in [
                    types.HarmCategory.HARM_CATEGORY_HATE_SPEECH,
                    types.HarmCategory.HARM_CATEGORY_DANGEROUS_CONTENT,
                    types.HarmCategory.HARM_CATEGORY_SEXUALLY_EXPLICIT,
                    types.HarmCategory.HARM_CATEGORY_HARASSMENT
                ]
            ],
            tools=[types.Tool(google_search=types.GoogleSearch()), types.Tool(url_context=types.UrlContext())], # Enable Google Search and URL Context tools
            thinking_config=types.ThinkingConfig(
                include_thoughts=False, # Can be True for debugging Gemini's thought process
                thinking_budget=Config.THINKING_BUDGET # Token budget for thinking
            )
        )
        logger.debug(f"⚙️ Created Gemini GenerateContentConfig.\nSystem instruction length (chars): {len(system_instruction_str)}")
        return config

class ResponseExtractor:
    """Extracts text content from various Gemini API response structures."""
    @staticmethod
    def extract_text(response: any) -> str:
        """Attempts to extract textual content from a Gemini API response."""
        # Attempt 1: Direct .text attribute (common for simple responses or if it's a property)
        try:
            if hasattr(response, 'text') and response.text and isinstance(response.text, str):
                return response.text.strip()
        except ValueError: # .text might raise ValueError in some scenarios if not applicable
            pass # Continue to next extraction method

        # Attempt 2: Standard path for GenerateContentResponse or ChatSession.send_message response
        try:
            if response.candidates and response.candidates[0].content and response.candidates[0].content.parts:
                texts = []
                for part in response.candidates[0].content.parts:
                    if hasattr(part, 'text') and part.text:
                        texts.append(part.text)
                    if part.function_call: # Log any function calls made by Gemini
                        args_str = json.dumps(dict(part.function_call.args), indent=2)
                        logger.info(f"🧠 Gemini Function Call executed by model:\nName: {part.function_call.name}\nArgs:\n{args_str}")
                        # Note: This bot does not currently *execute* these calls and send back results.
                        # It only logs that Gemini *would have* made them.
                if texts:




                    return '\n'.join(texts).strip()
        except (AttributeError, IndexError, ValueError) as e: # Catch common issues with this path
            logger.debug(f"🔍 Could not extract text using primary candidate path.\nError:\n{e}")

        # Attempt 3: Fallback for direct parts if response is a Content object itself
        try:
            if hasattr(response, 'parts') and response.parts:
                texts = [p.text for p in response.parts if hasattr(p, 'text') and p.text]
                if texts:
                    return '\n'.join(texts).strip()
        except (AttributeError, ValueError):
            pass # Continue to final fallback

        # If all extraction methods fail
        logger.error(f"❌ Failed to extract text from Gemini response.\nType:\n{type(response)}\nFull Response:\n{str(response)}")
        return "I encountered an issue processing the response format from the AI."

class MessageProcessor:
    """Core class for processing incoming Discord messages and interacting with Gemini."""
    SPEAK_TAG_PATTERN = re.compile(r"\[SPEAK(?::([A-Z_]+))?\]\s*(.*)", re.IGNORECASE | re.DOTALL)

    @staticmethod
    async def _build_gemini_prompt_parts(message: discord.Message, metadata_header_str: str, cleaned_content: str, reply_chain_data: list[dict]) -> tuple[list[types.Part | str], bool]:
        """
        Constructs the list of parts (metadata, text, files, YouTube) to send to Gemini.
        Returns a tuple containing the list of parts and a boolean indicating if the parts
        (beyond the initial metadata header) are substantively empty.
        """
        parts: list[types.Part | str] = [types.Part(text=metadata_header_str)]
        logger.debug(f"💬 Building message parts for Gemini.\nCurrent message content (cleaned):\n{cleaned_content}")

        # 1. Add textual context from the reply chain (if any)
        if reply_chain_data:
            textual_reply_context = ReplyChainProcessor.format_context_for_llm(reply_chain_data, message.id, bot.user.id)
            if textual_reply_context.strip():
                parts.append(types.Part(text=textual_reply_context))

        # 2. Process attachments from the *directly replied-to message* (if relevant)
        if message.reference and message.reference.message_id and len(reply_chain_data) > 1:
            replied_to_msg_data = reply_chain_data[-2] # -1 is current, -2 is the one replied to
            if replied_to_msg_data['author_id'] != bot.user.id and replied_to_msg_data['attachments']:
                logger.info(f"📎 Processing {len(replied_to_msg_data['attachments'])} attachment(s) from replied-to message.\nMessage ID: {replied_to_msg_data['message_obj'].id}")
                replied_attachments_parts = await AttachmentProcessor.process_discord_attachments(replied_to_msg_data['attachments'])
                if replied_attachments_parts: parts.extend(p for p in replied_attachments_parts if p)

        # 3. Process YouTube links from the current message's cleaned content
        content_after_youtube, youtube_file_data_parts = YouTubeProcessor.process_content(cleaned_content)
        if youtube_file_data_parts: parts.extend(youtube_file_data_parts)

        # 4. Add the textual content of the current message (if any remains after YouTube processing)

        current_message_text_placeholder = "User Message:"
        if content_after_youtube.strip():
            parts.append(types.Part(text=f"{current_message_text_placeholder} {content_after_youtube.strip()}"))
        else:



            if message.attachments: # If there are attachments for the current message, ensure a text part exists for context
                 parts.append(types.Part(text=f"{current_message_text_placeholder} [See attached files]"))

        # 5. Process attachments from the current message
        if message.attachments:
            logger.info(f"📎 Processing {len(message.attachments)} attachment(s) from current message.\nMessage ID: {message.id}")
            current_message_attachment_parts = await AttachmentProcessor.process_discord_attachments(list(message.attachments))
            if current_message_attachment_parts: parts.extend(p for p in current_message_attachment_parts if p)

        # Filter out any completely empty string parts or placeholder-only text parts
        final_parts = [p for p in parts if p and not (isinstance(p, str) and not p.strip())]

        final_parts = [
            p for p in final_parts
            if not (isinstance(p, types.Part) and p.text and p.text.strip() == current_message_text_placeholder)
        ]


        # Check if parts (excluding initial metadata header) are substantively empty
        is_substantively_empty_beyond_metadata = True
        if len(final_parts) > 1: # More than just the metadata header
            has_substantive_content = False
            for part_item in final_parts[1:]:
                if isinstance(part_item, types.Part):
                    if part_item.text and part_item.text.strip():

                        is_placeholder_only_variant = part_item.text.startswith(current_message_text_placeholder) and \
                                                  not part_item.text.replace(current_message_text_placeholder, "").replace("[See attached files]", "").strip()
                        if not is_placeholder_only_variant and not part_item.text.startswith("[Attachment:"):
                            has_substantive_content = True; break
                    if part_item.file_data:
                        has_substantive_content = True; break


                elif isinstance(part_item, str) and part_item.strip() and not part_item.startswith("[Attachment:"):
                     has_substantive_content = True; break
            is_substantively_empty_beyond_metadata = not has_substantive_content
        else:
            is_substantively_empty_beyond_metadata = True


        if is_substantively_empty_beyond_metadata:
             logger.warning("⚠️ No substantive parts (beyond metadata and basic context placeholders) were built for Gemini.")
        else:
            logger.info(f"💬 Final assembled parts for Gemini (Count: {len(final_parts)}):")
            for i, part_item in enumerate(final_parts):

                if isinstance(part_item, str):
                    logger.info(f"  Part {i+1} [Raw String]:\n{part_item}")
                elif hasattr(part_item, 'text') and isinstance(part_item.text, str):
                    logger.info(f"  Part {i+1} [Text Part]:\n{part_item.text}")
                elif hasattr(part_item, 'file_data') and part_item.file_data:
                    logger.info(f"  Part {i+1} [FileData]:\nURI: {part_item.file_data.file_uri}\nMIME: {part_item.file_data.mime_type}")
                else:
                    logger.info(f"  Part {i+1} [Other Part Type]:\n{str(part_item)}")
        return final_parts, is_substantively_empty_beyond_metadata


    @staticmethod
    async def process(message: discord.Message, bot_message_to_edit: discord.Message | None = None):
        global chat_history_manager # Use the global instance
        content_for_llm = re.sub(r'<[@#&!][^>]+>', '', message.content).strip() # Strip mentions, etc.

        guild_id_for_history = message.guild.id if message.guild else None
        user_id_for_history = message.author.id if guild_id_for_history is None else None # Use author ID for DMs

        reset_command_str = f"{bot.command_prefix}reset"
        if message.content.strip().lower().startswith(reset_command_str):
            deleted_count_msg = "No active history found to clear."
            if await chat_history_manager.delete_history(guild_id_for_history, user_id_for_history):
                if guild_id_for_history:
                    deleted_count_msg = f"🧹 Cleared chat history for this server ({message.guild.name})!"
                else:
                    deleted_count_msg = "🧹 Your DM chat history with me has been reset!"

            bot_response_message = await MessageSender.send(message, deleted_count_msg, None)
            if bot_response_message: active_bot_responses[message.id] = bot_response_message
            return

        async with message.channel.typing(): # Show "Bot is typing..."
            try:
                history_list = await chat_history_manager.load_history(guild_id_for_history, user_id_for_history)
                combined_system_prompt = PromptManager.load_combined_system_prompt()
                gemini_gen_config = GeminiConfigManager.create_config(combined_system_prompt)

                # Instantiate a chat session for this specific interaction
                current_chat_session = gemini_client.aio.chats.create(
                    model=Config.MODEL_ID,
                    config=gemini_gen_config,
                    history=history_list # Pass the loaded history
                )

                metadata_header = PromptManager.generate_per_message_metadata_header(message)
                reply_chain_data = await ReplyChainProcessor.get_chain(message) # Get reply chain context
                gemini_parts_for_prompt, is_substantively_empty_beyond_metadata = await MessageProcessor._build_gemini_prompt_parts(
                    message, metadata_header, content_for_llm, reply_chain_data
                )

                if is_substantively_empty_beyond_metadata and not history_list: # If no content AND no history, send a greeting
                    logger.info("💬 Message content was substantively empty (beyond metadata), and no history. Sending default greeting.")
                    bot_response_msg = await MessageSender.send(message,"Hello! How can I help you today?",None,existing_bot_message_to_edit=bot_message_to_edit)
                    if bot_response_msg: active_bot_responses[message.id] = bot_response_msg
                    return

                # Ensure all parts are types.Part for the API

                final_api_parts: list[types.Part] = []
                for p_item in gemini_parts_for_prompt:
                    if isinstance(p_item, str): # Should be rare now
                        final_api_parts.append(types.Part(text=p_item))
                    elif isinstance(p_item, types.Part):
                        final_api_parts.append(p_item)
                    else:

                        logger.warning(f"⚠️ Encountered unexpected item type ({type(p_item)}) when finalizing parts for Gemini API. Skipping.\nItem:\n{str(p_item)}")
                        continue

                if not final_api_parts: # Should not happen if metadata_header is always present


                    logger.error("❌ All prompt parts were unexpectedly skipped or invalid before sending to Gemini. Aborting this request.")
                    error_reply_msg = await MessageSender.send(message, "❌ I encountered an internal error preparing your request.", None, existing_bot_message_to_edit=bot_message_to_edit)
                    if error_reply_msg: active_bot_responses[message.id] = error_reply_msg
                    return

                logger.info(f"🧠 Sending parts to Gemini. Part Count: {len(final_api_parts)}. History Length (turns): {len(history_list)//2}.") # Each turn is user+model

                response_from_gemini = await current_chat_session.send_message(final_api_parts)


                # Manually update history list for ChatSession (since it was created fresh)
                # 1. Add user's turn (represented by final_api_parts which includes metadata, context, message)
                user_turn_content = types.Content(role="user", parts=final_api_parts) # Use the processed parts
                history_list.append(user_turn_content)

                # 2. Add model's turn
                if response_from_gemini.candidates and response_from_gemini.candidates[0].content:
                    model_turn_content = response_from_gemini.candidates[0].content

                    if model_turn_content.role != "model": # Should always be "model"
                        logger.warning(f"🧠 Model response content had unexpected role. Forcing to 'model'.\nActual Role:\n{model_turn_content.role}")

                        model_turn_content = types.Content(role="model", parts=model_turn_content.parts)
                    history_list.append(model_turn_content)
                else:
                    logger.error("❌ No valid content found in Gemini response to form model's turn in history.")

                    history_list.append(types.Content(role="model", parts=[types.Part(text="[Error: No response from model or malformed response]")]))

                await chat_history_manager.save_history(guild_id_for_history, user_id_for_history, history_list)

                response_text = ResponseExtractor.extract_text(response_from_gemini)
                final_text_for_discord = response_text
                ogg_audio_data, audio_duration, audio_waveform_b64 = None, 0.0, Config.DEFAULT_WAVEFORM_PLACEHOLDER

                speak_match = MessageProcessor.SPEAK_TAG_PATTERN.match(response_text)
                if speak_match:
                    style, text_after_tag = speak_match.groups() # style can be None
                    text_for_tts_generation = text_after_tag.strip()
                    if text_for_tts_generation: # Only generate TTS if there's text
                        tts_prompt_for_generator = f"In a {style.replace('_', ' ').lower()} tone, say: {text_for_tts_generation}" if style else text_for_tts_generation
                        tts_result = await TTSGenerator.generate_speech_ogg(tts_prompt_for_generator)
                        if tts_result:
                            ogg_audio_data, audio_duration, audio_waveform_b64 = tts_result
                            final_text_for_discord = None # Audio will be sent, suppress original text
                        else: # TTS failed, keep text
                            final_text_for_discord = text_for_tts_generation # Send the text that was meant for TTS
                    else: # Empty after [SPEAK] tag (e.g., "[SPEAK]")
                        # If the entire response was just the [SPEAK] tag, use a placeholder. Otherwise, remove the tag.
                        final_text_for_discord = response_text.replace(speak_match.group(0), "").strip()
                        if not final_text_for_discord and response_text.strip() == speak_match.group(0).strip():
                            final_text_for_discord = "..." # Placeholder if nothing remains
                        elif not final_text_for_discord: # If removing the tag results in empty string
                             final_text_for_discord = "..."

                new_or_edited_bot_message = await MessageSender.send(
                    message, final_text_for_discord, ogg_audio_data, audio_duration, audio_waveform_b64, bot_message_to_edit
                )
                if new_or_edited_bot_message: active_bot_responses[message.id] = new_or_edited_bot_message
                else: active_bot_responses.pop(message.id, None) # If sending failed, remove potential stale entry

            except Exception as e:
                logger.error(f"❌ Message processing pipeline error.\nUser: {message.author.name}\nError:\n{e}", exc_info=True)
                error_reply_msg = await MessageSender.send(message, "❌ I encountered an error processing your request.", None, existing_bot_message_to_edit=bot_message_to_edit)
                if error_reply_msg: active_bot_responses[message.id] = error_reply_msg
                else: active_bot_responses.pop(message.id, None)

# --- Discord Event Handlers ---
@bot.event
async def on_ready():
    logger.info(f"🎉 Logged in as {bot.user.name} (ID: {bot.user.id})")
    logger.info(f"🔗 Discord.py Version: {discord.__version__}")
    logger.info(f"🧠 Using Main Gemini Model: {Config.MODEL_ID}")
    logger.info(f"🗣️ Using TTS Gemini Model: {Config.TTS_MODEL_ID} with Voice: {Config.VOICE_NAME}")
    logger.info(f"💾 Chat History Max Turns (User+Assistant pairs): {Config.MAX_HISTORY_TURNS}")
    try:
        await bot.change_presence(activity=discord.Activity(type=discord.ActivityType.listening, name=f"messages | {bot.command_prefix}reset"))
    except Exception as e:
        logger.warning(f"⚠️ Could not set bot presence.\nError:\n{e}")

@bot.event
async def on_message(message: discord.Message):
    if message.author == bot.user or message.author.bot: return # Ignore self and other bots

    is_dm = isinstance(message.channel, discord.DMChannel)
    is_mentioned = bot.user.mentioned_in(message)
    is_reply_to_bot = False
    if message.reference and message.reference.message_id:
        try:
            # Fetch the referenced message to check its author
            referenced_message = await message.channel.fetch_message(message.reference.message_id)
            if referenced_message.author == bot.user:
                is_reply_to_bot = True
        except (discord.NotFound, discord.Forbidden, discord.HTTPException):
            pass # Couldn't fetch referenced message, assume not a reply to bot for safety

    is_reset_command = message.content.lower().startswith(f"{bot.command_prefix}reset")

    # Process if: it's a DM, or bot is mentioned, or it's a reply to the bot, or it's the reset command
    if is_dm or is_mentioned or is_reply_to_bot or is_reset_command:
        await MessageProcessor.process(message)

@bot.event
async def on_message_edit(before: discord.Message, after: discord.Message):
    if after.author == bot.user or after.author.bot: return # Ignore self and other bots' edits

    # Determine if the *edited* message qualifies for processing
    is_dm_after = isinstance(after.channel, discord.DMChannel)
    is_mentioned_after = bot.user.mentioned_in(after)
    is_reply_to_bot_after = False
    if after.reference and after.reference.message_id:
        try:
            referenced_message_after = await after.channel.fetch_message(after.reference.message_id)
            if referenced_message_after.author == bot.user:
                is_reply_to_bot_after = True
        except (discord.NotFound, discord.Forbidden, discord.HTTPException):
            pass

    is_reset_command_after = after.content.lower().startswith(f"{bot.command_prefix}reset")
    should_process_after = is_dm_after or is_mentioned_after or is_reply_to_bot_after or is_reset_command_after

    # Get the bot's response to the *original* user message, if it exists
    bot_response_to_original_message = active_bot_responses.pop(before.id, None)

    if should_process_after:
        logger.info(f"📥 Edited message qualifies for processing. Reprocessing as new message.\nMessage ID: {after.id}")
        if bot_response_to_original_message: # Delete the bot's old reply to the unedited message
            try:
                await bot_response_to_original_message.delete()
            except discord.HTTPException:
                logger.warning(f"⚠️ Could not delete previous bot response for edited message.\nBot Response ID: {bot_response_to_original_message.id}\nOriginal User Message ID: {before.id}")

        # Process the 'after' message as a new one.
        # Pass bot_message_to_edit=None because we've already deleted the old response.
        await MessageProcessor.process(after, bot_message_to_edit=None)
    else: # Edited message no longer qualifies for a response
        if bot_response_to_original_message:
            try:
                await bot_response_to_original_message.delete()

                logger.info(f"🗑️ Deleted bot response as original message was edited to no longer qualify.\nBot Response ID: {bot_response_to_original_message.id}\nOriginal User Message ID: {before.id}")
            except discord.HTTPException: pass # Non-critical if deletion fails

@bot.event
async def on_message_delete(message: discord.Message):
    # If a user message that the bot responded to is deleted, delete the bot's response.
    if message.id in active_bot_responses:
        bot_response_to_delete = active_bot_responses.pop(message.id, None)
        if bot_response_to_delete:
            try:
                await bot_response_to_delete.delete()
                logger.info(f"🗑️ Bot response deleted because original user message was deleted.\nBot Response ID: {bot_response_to_delete.id}\nUser Message ID: {message.id}")
            except discord.HTTPException: pass # Non-critical


# --- Setup and Main Execution ---
def validate_environment_variables():

    """Validates that essential environment variables are set."""
    if not DISCORD_BOT_TOKEN:
        raise ValueError("DISCORD_BOT_TOKEN not found in environment variables.")
    if not GEMINI_API_KEY:
        raise ValueError("GEMINI_API_KEY not found in environment variables.")
    logger.info("✅ Environment variables validated.")

def setup_logging():
    """Configures logging for the application."""
    # Basic config for root logger (if other libraries use logging)
    logging.basicConfig(level=logging.WARNING, format='%(asctime)s [%(levelname)s] [%(name)s] %(message)s', handlers=[logging.StreamHandler()], force=True)



    # Configure application-specific logger ("Bard")
    app_logger = logging.getLogger("Bard") # Get the logger defined at the top
    app_logger.setLevel(logging.INFO) # Set desired level (INFO or DEBUG)
    app_logger.propagate = False # Prevent messages from bubbling up to the root logger if it has handlers

    # Console Handler for "Bard" logger
    console_handler = logging.StreamHandler()
    # File Handler for "Bard" logger
    file_handler = logging.FileHandler('.log', mode='a', encoding='utf-8') # Append mode

    # Formatter for "Bard" logger handlers
    formatter = logging.Formatter('%(asctime)s [%(levelname)s] [%(name)s:%(module)s:%(funcName)s:%(lineno)d] %(message)s')
    console_handler.setFormatter(formatter)
    file_handler.setFormatter(formatter)

    # Add handlers to "Bard" logger
    app_logger.addHandler(console_handler)
    app_logger.addHandler(file_handler)

    # Set logging levels for noisy libraries
    logging.getLogger("discord").setLevel(logging.WARNING)
    logging.getLogger("discord.http").setLevel(logging.WARNING) # discord.py's HTTP traffic
    logging.getLogger("google.genai").setLevel(logging.WARNING)
    logging.getLogger("httpx").setLevel(logging.WARNING) # Gemini client uses httpx

    # Use the app_logger for this initial message
    app_logger.info("⚙️ Logging configured.")


def main():
    global gemini_client, chat_history_manager # Allow assignment to globals
    try:
        setup_logging() # Configure logging first
        logger.info("🚀 Initializing Gemini Discord Bot...") # Now logger is "Bard"
        validate_environment_variables()

        # Initialize Gemini client with specific API version for stability
        gemini_client = genai.Client(api_key=GEMINI_API_KEY, http_options={'api_version': 'v1beta'})
        chat_history_manager = ChatHistoryManager() # Initialize the history manager

        logger.info(f"🤖 Gemini AI Client initialized. Target API version: v1beta") # Using 'Bard' logger
        logger.info("📡 Starting Discord bot...") # Using 'Bard' logger
        bot.run(DISCORD_BOT_TOKEN, log_handler=None) # Disable discord.py's default log handler setup



    except ValueError as ve:
        # Logger might not be fully set up if error is in validate_environment_variables or early
        print(f"Configuration Error: {ve}")
        # Attempt to log critical error if logger is available
        if logger.handlers: logger.critical(f"💥 Configuration Error:\n{ve}", exc_info=True)
        return 1
    except discord.LoginFailure as lf:
        logger.critical(f"🛑 Discord Login Failed. Check bot token and intents.\nError:\n{lf}")
        print("❌ Discord Login Failed. Check bot token and intents.")
        return 1
    except Exception as e:
        logger.critical(f"💥 Fatal error during bot execution:\n{e}", exc_info=True)
        print(f"💥 Fatal error: {e}")
        return 1
    finally:
        # This will be logged by the 'Bard' logger if it was initialized
        logger.info("🛑 Bot shutdown sequence initiated.")
    return 0

if __name__ == "__main__":
    exit_code = main()
    # Final log messages; logger should be available if main() ran past setup_logging()
    if exit_code == 0:
        logger.info("✅ Bot exited gracefully.")
    else:
        logger.warning(f"⚠️ Bot exited with error code: {exit_code}.")
    logging.shutdown() # Cleanly close logging resources