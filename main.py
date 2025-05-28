import asyncio
import base64
import io
import logging
import os
import re
import tempfile
import wave
from datetime import datetime

import aiohttp
import discord
import numpy as np
import soundfile  # For getting duration and samples for waveform
from discord.ext import commands
from dotenv import load_dotenv
from google import genai
from google.genai import types
from google.genai.chats import Chat as GenAIChatSession # Use an alias to avoid potential name clashes

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


# --- Environment Setup ---
load_dotenv()
DISCORD_BOT_TOKEN = os.getenv("DISCORD_BOT_TOKEN")
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY")

# --- System Prompt ---
def load_system_prompt() -> str:
    """Loads the base system prompt from a file."""
    try:
        with open("system_prompt.md", "r", encoding="utf-8") as f:
            prompt = f.read()
            logger.info("Successfully loaded system_prompt.md.")
            return prompt
    except FileNotFoundError:
        logger.warning("system_prompt.md not found. Using a minimal fallback prompt.")
        return "You are a helpful AI assistant on Discord. Be concise and helpful."
    except Exception as e:
        logger.error(f"Error loading system_prompt.md: {e}", exc_info=True)
        return "You are a helpful AI assistant on Discord. Be concise and helpful."

# --- Global State ---
chat_sessions = {}  # Stores active chat sessions (guild_id_user_id -> GeminiChatSession)
active_bot_responses = {} # Stores user_message_id -> bot_response_message_object
gemini_client = None # Initialized in main()

# --- Discord Bot Setup ---
intents = discord.Intents.default()
intents.message_content = True  # Required for reading message content
bot = commands.Bot(command_prefix="!", intents=intents)


# --- Utility Classes ---
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
            logger.debug("Detected RIFF container, but not specifically WEBP or WAV. Falling back.")
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
            # This is a broad check. Specific video/audio distinction might need more.
            # For Gemini, 'video/mp4' or 'audio/mp4' might be acceptable.
            # Assuming video if not clearly audio from other signatures.
            logger.debug("Detected MP4-based container (ftyp/moov). Assuming 'video/mp4'.")
            return 'video/mp4'


        logger.debug("MIME type not identified by known signatures. Defaulting to 'application/octet-stream'.")
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
            # Find all matches, then extract the full URL of the match
            matches = pattern.finditer(text)
            for match in matches:
                found_urls.append(match.group(0)) # Get the full matched URL
        return list(set(found_urls)) # Return unique URLs

    @classmethod
    def process_content(cls, content: str) -> tuple[str, list[types.Part]]:
        """
        Extracts YouTube URLs, creates FileData parts, and returns cleaned content.

        Args:
            content: The text content to process.

        Returns:
            A tuple containing:
                - The content string with YouTube URLs removed.
                - A list of Gemini `types.Part` objects for each YouTube URL.
        """
        urls = cls.extract_urls(content)
        if not urls:
            return content, []

        youtube_parts = []
        for url in urls:
            try:
                # Ensure we use the full URL for file_uri
                youtube_parts.append(types.Part(file_data=types.FileData(mime_type="video/youtube", file_uri=url)))
            except Exception as e:
                logger.error(f"Error creating FileData for YouTube URL {url}: {e}", exc_info=True)


        cleaned_content = content
        for url in urls: # Remove all occurrences of the processed URLs
            cleaned_content = cleaned_content.replace(url, "")

        # Consolidate whitespace that might be left after URL removal
        cleaned_content = re.sub(r'\s+', ' ', cleaned_content).strip()

        if youtube_parts:
            logger.info(f"🎥 Identified {len(youtube_parts)} YouTube video link(s) for model processing: {urls}")
        return cleaned_content, youtube_parts

class SystemPromptBuilder:
    """Builds the detailed system prompt for the Gemini model."""
    @staticmethod
    def build(user: discord.User, channel: discord.abc.Messageable, base_prompt: str) -> str:
        """
        Constructs the system prompt with dynamic metadata.

        Args:
            user: The Discord user who sent the message.
            channel: The Discord channel where the message was sent.
            base_prompt: The base system prompt loaded from the file.

        Returns:
            The fully constructed system prompt string.
        """
        channel_name = 'DM'
        guild_name = 'N/A (Direct Message)'
        if hasattr(channel, 'guild') and channel.guild is not None:
            guild_name = channel.guild.name
            if isinstance(channel, discord.Thread):
                channel_name = f"{channel.parent.name}/{channel.name}"
            elif hasattr(channel, 'name'): # TextChannel, VoiceChannel, etc.
                channel_name = channel.name
            else: # Fallback if channel name attribute is missing
                channel_name = f"Unknown Channel (ID: {channel.id})"


        metadata = f"""
--- METADATA & CAPABILITIES ---
Date: {datetime.now().strftime('%Y-%m-%d %H:%M:%S UTC')}
User: {user.display_name} (@{user.name}, ID: {user.id})
User Mention: <@{user.id}>
Guild: {guild_name}
Channel: #{channel_name}
Platform: Discord

YOUR CAPABILITIES:
- You can use the following Markdown formatting when appropriate:
*italics*, __*underline italics*__, **bold**, __**underline bold**__, ***bold italics***, __***underline bold italics***__, __underline__,  ~~Strikethrough~~,
# Big Header, ## Smaller Header, ### Smallest Header, -# Subtext, [Masked Links](https://example.url/),
- Lists,
  - Indented List,
1 Numbered lists, (must be separated by two new-lines to change from regular list to numbered list)
`code block`,
```code language
multi-line
code block
```,
> Block quotes,
>>> Multi-line quote blocks, (only needed on the first line of a multi-line quote block, to end simply use two new-lines),
||Spoiler tags|| (negated by code blocks!)

- You can understand text, images, audio clips, videos (including YouTube links), and PDF documents provided by the user, including those in replied-to messages if relevant.
- You have access to Google Search for up-to-date information. When asked about events, always refer to Google.
- You can analyze content from web URLs provided by the user if they provide the URL.
- You can generate audio and send it as a file.
- If you believe a spoken response is appropriate, or if the user requests it, begin your textual response with a special tag:
    - `[SPEAK] Your text here.`
    - `[SPEAK:STYLE] Your text here.` (e.g., `[SPEAK:CHEERFUL]`, `[SPEAK:SAD]`, `[SPEAK:ANGRY]`).
    - In this case, only respond with the intended spoken text. Do not include any other comments, as the resulting text will be entirely generated to speech.
- Respond with appropriate length. Simple topics should be answered in maximum 1-2 sentences, and around 1 paragraph for more complex topics. Judge this based on the complexity of the topic, not the question.
- Do not comment on this metadata section or your capabilities unless specifically asked about them.
--- END METADATA ---
"""
        final_prompt = base_prompt + "\n" + metadata
        logger.debug(f"Constructed system prompt for user {user.name} in channel {channel_name}:\n{final_prompt}")
        return final_prompt

class GeminiConfigManager:
    """Manages the generation configuration for Gemini API calls."""
    @staticmethod
    def create_config(user: discord.User, channel: discord.abc.Messageable) -> types.GenerateContentConfig:
        """
        Creates the Gemini generation configuration.

        Args:
            user: The Discord user.
            channel: The Discord channel.

        Returns:
            A `types.GenerateContentConfig` object.
        """
        system_instruction = SystemPromptBuilder.build(user, channel, load_system_prompt())
        config = types.GenerateContentConfig(
            system_instruction=system_instruction,
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
            tools=[
                types.Tool(google_search=types.GoogleSearch()), # Enable Google Search tool
                types.Tool(url_context=types.UrlContext()), # Enable URL Context tool
            ],
            thinking_config=types.ThinkingConfig(
                include_thoughts=False, # Set True for debugging model reasoning (may change response structure)
                thinking_budget=Config.THINKING_BUDGET
            )
        )
        logger.debug(f"Created Gemini GenerateContentConfig. System instruction length: {len(system_instruction)} chars.")
        return config

class ResponseExtractor:
    """Extracts text content from various Gemini API response structures."""
    @staticmethod
    def extract_text(response: any) -> str:
        """
        Attempts to extract textual content from a Gemini API response.

        Args:
            response: The Gemini API response object.

        Returns:
            The extracted text string, or an error message if extraction fails.
        """
        logger.debug(f"Attempting to extract text from Gemini response of type: {type(response)}")
        # logger.debug(f"Full Gemini Response for extraction: {response}") # Can be very verbose

        # Path 1: Direct .text attribute (common for simple text parts or older response types)
        try:
            if hasattr(response, 'text') and response.text and isinstance(response.text, str):
                logger.debug("Extracted text using direct '.text' attribute.")
                return response.text.strip()
        except ValueError: # .text might be a property raising an error if not applicable
            logger.debug("Direct '.text' attribute access raised ValueError or was not applicable.")
            pass # Continue to other extraction methods

        # Path 2: .parts from a single content object (e.g., a part in chat history)
        try:
            if hasattr(response, 'parts') and response.parts:
                texts = [p.text for p in response.parts if hasattr(p, 'text') and p.text and isinstance(p.text, str)]
                if texts:
                    extracted = '\n'.join(texts).strip()
                    logger.debug(f"Extracted text from '.parts' attribute: \"{extracted[:100]}...\"")
                    return extracted
        except (AttributeError, ValueError) as e:
            logger.debug(f"Error or not applicable when extracting from '.parts': {e}")
            pass

        # Path 3: .candidates from a GenerateContentResponse structure
        try:
            if hasattr(response, 'candidates') and response.candidates:
                candidate = response.candidates[0] # Assuming the first candidate is the primary one
                if hasattr(candidate, 'content') and hasattr(candidate.content, 'parts') and candidate.content.parts:
                    texts = [
                        p.text for p in candidate.content.parts
                        if hasattr(p, 'text') and p.text and isinstance(p.text, str)
                    ]
                    if texts:
                        extracted = '\n'.join(texts).strip()
                        logger.debug(f"Extracted text from 'response.candidates[0].content.parts': \"{extracted[:100]}...\"")
                        # Log tool calls if present
                        for part in candidate.content.parts:
                            if part.function_call:
                                logger.info(f"Gemini Function Call: {part.function_call.name} with args: {part.function_call.args}")
                            if part.tool_code_output: # This is for when WE send tool output back
                                logger.info(f"Gemini Tool Code Output (from our input): {part.tool_code_output}")

                        # Check for tool_code_output in the response itself (if Gemini is reporting its own tool use)
                        # This part of the genai API is less common for direct chat, usually handled by the client library.
                        # If Gemini uses a tool like Google Search, the result is typically embedded in the text part.
                        # However, if `response.tool_code_output` or similar exists, log it.
                        if hasattr(candidate, 'tool_code_output'): # Hypothetical, check actual API structure
                             logger.info(f"Gemini Response Tool Output: {candidate.tool_code_output}")


                        return extracted
        except (AttributeError, ValueError, IndexError) as e:
            logger.debug(f"Error or not applicable when extracting from 'response.candidates': {e}")
            pass

        logger.error(f"Failed to extract text from Gemini response. Type: {type(response)}. Full Response (abbreviated): {str(response)[:500]}")
        return "I encountered an issue processing the response format from the AI."

class TTSGenerator:
    """Generates speech audio using Gemini TTS and converts it to OGG Opus."""

    @staticmethod
    async def _convert_to_ogg_opus(input_wav_path: str, output_ogg_path: str) -> bool:
        """
        Converts a WAV file to OGG Opus format using ffmpeg.

        Args:
            input_wav_path: Path to the input WAV file.
            output_ogg_path: Path to save the output OGG Opus file.

        Returns:
            True if conversion was successful, False otherwise.
        """
        try:
            command = [
                Config.FFMPEG_PATH, '-y', '-i', input_wav_path,
                '-c:a', 'libopus',    # Opus codec
                '-b:a', '32k',        # Bitrate (adjust as needed, 32k is decent for voice)
                '-ar', '48000',       # Opus standard sample rate for Discord
                '-ac', '1',           # Mono channel for Discord voice
                '-application', 'voip', # Optimize for voice
                '-vbr', 'on',         # Variable bitrate
                output_ogg_path
            ]
            logger.info(f"Executing ffmpeg: {' '.join(command)}")
            process = await asyncio.create_subprocess_exec(
                *command,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE
            )
            stdout, stderr = await process.communicate()

            if process.returncode != 0:
                logger.error(f"ffmpeg conversion failed for {input_wav_path} (Code: {process.returncode}):"
                             f"\nStdout: {stdout.decode(errors='ignore')}"
                             f"\nStderr: {stderr.decode(errors='ignore')}")
                return False
            logger.info(f"Successfully converted {input_wav_path} to {output_ogg_path}")
            return True
        except FileNotFoundError:
            logger.error(f"ffmpeg command ('{Config.FFMPEG_PATH}') not found. Please ensure ffmpeg is installed and in your system's PATH or FFMPEG_PATH env var is set.")
            return False
        except Exception as e:
            logger.error(f"Error during ffmpeg conversion: {e}", exc_info=True)
            return False

    @staticmethod
    def _get_audio_duration_and_waveform(audio_path: str, max_waveform_points: int = 128) -> tuple[float, str]:
        """
        Gets audio duration and generates a simple base64 encoded waveform string.

        Args:
            audio_path: Path to the audio file (WAV or OGG).
            max_waveform_points: Maximum number of points for the waveform visualization.

        Returns:
            A tuple containing:
                - Duration of the audio in seconds.
                - Base64 encoded string representing the waveform.
        """
        try:
            audio_data, samplerate = soundfile.read(audio_path)
            duration_secs = len(audio_data) / float(samplerate)

            # Ensure mono for waveform calculation
            if audio_data.ndim > 1:
                mono_audio_data = np.mean(audio_data, axis=1)
            else:
                mono_audio_data = audio_data

            num_samples = len(mono_audio_data)
            if num_samples == 0:
                logger.warning(f"No audio samples found in {audio_path} for waveform generation.")
                return duration_secs, Config.DEFAULT_WAVEFORM_PLACEHOLDER

            # Ensure audio_data is normalized to [-1, 1] for RMS calculation if it's not already
            # soundfile typically returns float data in [-1, 1] range.
            # If it's integer type, it would need normalization. Assuming float here.
            if np.issubdtype(mono_audio_data.dtype, np.integer):
                 mono_audio_data = mono_audio_data / np.iinfo(mono_audio_data.dtype).max


            step = max(1, num_samples // max_waveform_points)
            waveform_raw_bytes = bytearray()

            for i in range(0, num_samples, step):
                chunk = mono_audio_data[i:i+step]
                if len(chunk) == 0: continue
                # RMS of the chunk, scaled to 0-1 range, then to 0-255 byte value
                # The factor of 5.0 is arbitrary for visual scaling, adjust if needed
                rms = np.sqrt(np.mean(chunk**2))
                scaled_value = int(min(rms * 5.0, 1.0) * 255) # Scale and cap at 255
                waveform_raw_bytes.append(scaled_value)

            if not waveform_raw_bytes:
                logger.warning(f"Waveform generation resulted in empty byte array for {audio_path}.")
                return duration_secs, Config.DEFAULT_WAVEFORM_PLACEHOLDER

            waveform_b64 = base64.b64encode(waveform_raw_bytes).decode('utf-8')
            logger.debug(f"Generated waveform for {audio_path} with {len(waveform_raw_bytes)} points. Duration: {duration_secs:.2f}s.")
            return duration_secs, waveform_b64
        except Exception as e:
            logger.error(f"Error getting duration/waveform for {audio_path}: {e}", exc_info=True)
            # Fallback to trying to get duration if waveform fails
            try:
                info = soundfile.info(audio_path)
                logger.warning(f"Waveform generation failed for {audio_path}, but got duration: {info.duration:.2f}s.")
                return info.duration, Config.DEFAULT_WAVEFORM_PLACEHOLDER
            except Exception as e_info:
                logger.error(f"Fallback to get duration also failed for {audio_path}: {e_info}", exc_info=True)
                return 1.0, Config.DEFAULT_WAVEFORM_PLACEHOLDER # Default non-zero duration

    @staticmethod
    async def generate_speech_ogg(text_for_tts: str) -> tuple[bytes, float, str] | None:
        """
        Generates speech audio in OGG Opus format from text using Gemini TTS.

        Args:
            text_for_tts: The text to synthesize.

        Returns:
            A tuple (ogg_opus_bytes, duration_seconds, waveform_base64) if successful,
            None otherwise.
        """
        global gemini_client
        if not gemini_client:
            logger.error("Gemini client not initialized. Cannot generate TTS.")
            return None

        tmp_wav_path = None
        tmp_ogg_path = None

        try:
            logger.info(f"🎤 Generating TTS (WAV) for: \"{text_for_tts[:100]}...\" with voice {Config.VOICE_NAME} using model {Config.TTS_MODEL_ID}")

            # Configuration specific to TTS generation
            speech_generation_config = types.GenerateContentConfig(
                response_modalities=["AUDIO"], # Request audio output
                speech_config=types.SpeechConfig(
                    voice_config=types.VoiceConfig(
                        prebuilt_voice_config=types.PrebuiltVoiceConfig(voice_name=Config.VOICE_NAME)
                    )
                )
            )
            # Log the request being sent to Gemini TTS
            logger.debug(f"Sending to Gemini TTS model '{Config.TTS_MODEL_ID}': Prompt='{text_for_tts[:100]}...', Config={speech_generation_config}")

            response = await gemini_client.aio.models.generate_content(
                model=Config.TTS_MODEL_ID,
                contents=text_for_tts, # The text prompt for TTS
                config=speech_generation_config
            )
            logger.debug(f"Received response from Gemini TTS model. Candidates: {len(response.candidates) if response.candidates else 'None'}")


            wav_data = None
            try:
                # Standard path for audio data in Gemini response
                if (response.candidates and response.candidates[0].content and
                    response.candidates[0].content.parts and
                    response.candidates[0].content.parts[0].inline_data and
                    response.candidates[0].content.parts[0].inline_data.data):
                    wav_data = response.candidates[0].content.parts[0].inline_data.data
                    logger.info(f"Successfully extracted {len(wav_data)} bytes of WAV data from Gemini TTS response.")
                else:
                    logger.error("Audio data not found at expected path in Gemini TTS response.")
                    if hasattr(response, 'candidates') and response.candidates:
                        logger.error(f"TTS Response Candidate 0 Content: {response.candidates[0].content if response.candidates[0] else 'N/A'}")
                    else:
                        logger.error(f"Full TTS Response (or prompt feedback): {str(response)[:500]}") # Log abbreviated full response
            except (AttributeError, IndexError) as e_access:
                logger.error(f"Error accessing Gemini TTS response data: {e_access}. Response: {str(response)[:500]}", exc_info=True)

            if not wav_data:
                logger.error("No WAV audio data extracted from Gemini TTS response after checks.")
                return None

            # Create temporary files for WAV and OGG
            with tempfile.NamedTemporaryFile(suffix='.wav', delete=False) as tmp_wav_file_obj, \
                 tempfile.NamedTemporaryFile(suffix='.ogg', delete=False) as tmp_ogg_file_obj:
                tmp_wav_path = tmp_wav_file_obj.name
                tmp_ogg_path = tmp_ogg_file_obj.name
            logger.debug(f"Created temporary files: WAV='{tmp_wav_path}', OGG='{tmp_ogg_path}'")


            # Write WAV data to the temporary file
            with wave.open(tmp_wav_path, 'wb') as wf:
                wf.setnchannels(Config.TTS_CHANNELS)
                wf.setsampwidth(Config.TTS_SAMPLE_WIDTH)
                wf.setframerate(Config.TTS_SAMPLE_RATE)
                wf.writeframes(wav_data)
            logger.info(f"WAV data written to {tmp_wav_path} ({os.path.getsize(tmp_wav_path)} bytes).")


            # Convert WAV to OGG Opus
            if not await TTSGenerator._convert_to_ogg_opus(tmp_wav_path, tmp_ogg_path):
                logger.error(f"Failed to convert {tmp_wav_path} to OGG Opus at {tmp_ogg_path}.")
                return None

            # Get duration and waveform from the OGG file
            duration_secs, waveform_b64 = TTSGenerator._get_audio_duration_and_waveform(tmp_ogg_path)

            # Read the OGG Opus bytes
            with open(tmp_ogg_path, 'rb') as f_ogg:
                ogg_opus_bytes = f_ogg.read()

            logger.info(f"🎤 OGG Opus generated successfully. Size: {len(ogg_opus_bytes)} bytes. Duration: {duration_secs:.2f}s. Waveform points: {len(base64.b64decode(waveform_b64)) if waveform_b64 != Config.DEFAULT_WAVEFORM_PLACEHOLDER else 'Placeholder'}")
            return ogg_opus_bytes, duration_secs, waveform_b64

        except Exception as e:
            logger.error(f"TTS generation or OGG conversion pipeline error: {e}", exc_info=True)
            return None
        finally:
            # Cleanup temporary files
            for f_path in [tmp_wav_path, tmp_ogg_path]:
                if f_path and os.path.exists(f_path):
                    try:
                        os.unlink(f_path)
                        logger.debug(f"Deleted temporary file: {f_path}")
                    except OSError as e_unlink:
                        logger.warning(f"Could not delete temporary file {f_path}: {e_unlink}")

class MessageSender:
    """Handles sending messages (text and voice) to Discord."""

    @staticmethod
    async def _send_text_reply(message_to_reply_to: discord.Message, text_content: str) -> discord.Message | None:
        """Sends a text reply, handling Discord's message length limits. Returns the primary sent message."""
        primary_sent_message = None
        if not text_content or not text_content.strip():
            logger.warning("Attempted to send empty text reply. Sending a placeholder.")
            text_content = "I processed your request but have no further text to add."

        if len(text_content) > Config.MAX_MESSAGE_LENGTH:
            logger.info(f"Message content ({len(text_content)} chars) exceeds Discord limit. Sending in chunks.")
            first_chunk = text_content[:Config.MAX_MESSAGE_LENGTH]
            remaining_text = text_content[Config.MAX_MESSAGE_LENGTH:]
            try:
                sent_msg = await message_to_reply_to.reply(first_chunk)
                if not primary_sent_message: primary_sent_message = sent_msg
            except discord.HTTPException as e:
                logger.error(f"Failed to send reply (chunk 1): {e}. Attempting to send to channel directly.", exc_info=True)
                try:
                    sent_msg = await message_to_reply_to.channel.send(first_chunk)
                    if not primary_sent_message: primary_sent_message = sent_msg
                except discord.HTTPException as e_chan:
                     logger.error(f"Failed to send to channel directly (chunk 1): {e_chan}", exc_info=True)


            # Send remaining chunks as new messages in the channel
            current_chunk = ""
            for paragraph in remaining_text.split('\n\n'): # Split by paragraphs for better readability
                if len(current_chunk + paragraph + '\n\n') > Config.MAX_MESSAGE_LENGTH:
                    if current_chunk.strip():
                        try: await message_to_reply_to.channel.send(current_chunk.strip())
                        except discord.HTTPException as e: logger.error(f"Failed to send chunk: {e}", exc_info=True)
                    current_chunk = paragraph + '\n\n'
                else:
                    current_chunk += paragraph + '\n\n'
            if current_chunk.strip():
                try: await message_to_reply_to.channel.send(current_chunk.strip())
                except discord.HTTPException as e: logger.error(f"Failed to send final chunk: {e}", exc_info=True)
        else:
            try:
                sent_msg = await message_to_reply_to.reply(text_content)
                if not primary_sent_message: primary_sent_message = sent_msg
            except discord.HTTPException as e:
                logger.error(f"Failed to send reply: {e}. Attempting to send to channel directly.", exc_info=True)
                try:
                    sent_msg = await message_to_reply_to.channel.send(text_content)
                    if not primary_sent_message: primary_sent_message = sent_msg
                except discord.HTTPException as e_chan:
                    logger.error(f"Failed to send to channel directly: {e_chan}", exc_info=True)
        if primary_sent_message:
            logger.info(f"Sent text reply (ID: {primary_sent_message.id}) to {message_to_reply_to.author.name} in #{message_to_reply_to.channel}. Content (start): \"{text_content[:100]}...\"")
        return primary_sent_message


    @staticmethod
    async def send(message_to_reply_to: discord.Message,
                     text_content: str | None,
                     audio_data: bytes | None = None,
                     duration_secs: float = 0.0,
                     waveform_b64: str = Config.DEFAULT_WAVEFORM_PLACEHOLDER,
                     existing_bot_message_to_edit: discord.Message | None = None) -> discord.Message | None:
        """
        Sends a reply to a Discord message. Can be text, voice, or both.
        Returns the primary discord.Message object that was sent, or None.
        """
        can_try_native_voice = audio_data and DISCORD_BOT_TOKEN and (not text_content or not text_content.strip())
        temp_ogg_file_path_for_upload = None # Needs to be accessible in finally

        if existing_bot_message_to_edit:
            # New response is purely text. Attempt to edit.
            if text_content and not audio_data:
                try:
                    # Check if the existing message is suitable for a simple content edit
                    # (e.g., not a voice message or a message that primarily relies on an attachment)
                    is_simple_text_message = not existing_bot_message_to_edit.attachments and \
                                             not (existing_bot_message_to_edit.flags and existing_bot_message_to_edit.flags.value & 8192) # 8192 is voice message flag

                    if is_simple_text_message:
                        logger.info(f"Attempting to edit existing bot message (ID: {existing_bot_message_to_edit.id}) with new text content.")
                        # Ensure text_content respects Discord's length limits for an edit
                        if len(text_content) > Config.MAX_MESSAGE_LENGTH:
                             # If new text is too long, split it. Edit with first part, send rest as new.
                             logger.warning(f"New text content for edit (len: {len(text_content)}) exceeds max length. Edit may fail or be truncated by Discord.")

                        await existing_bot_message_to_edit.edit(content=text_content)
                        logger.info(f"Successfully edited bot message (ID: {existing_bot_message_to_edit.id}) with new text.")
                        return existing_bot_message_to_edit
                    else:
                        logger.info(f"Existing bot message (ID: {existing_bot_message_to_edit.id}) is not a simple text message. Falling back to delete and resend.")
                        # Fall through to delete and resend logic by not returning here.

                except discord.HTTPException as e:
                    logger.error(f"Failed to edit bot message (ID: {existing_bot_message_to_edit.id}) with text: {e}. Falling back to delete and resend.", exc_info=True)
                except Exception as e_unhandled: # Catch any other errors during edit attempt
                    logger.error(f"Unhandled error editing bot message (ID: {existing_bot_message_to_edit.id}): {e_unhandled}. Falling back.", exc_info=True)

                # If edit attempt failed or wasn't suitable, delete the old message before resending.
                try:
                    await existing_bot_message_to_edit.delete()
                    logger.info(f"Deleted old bot message (ID: {existing_bot_message_to_edit.id}) for fallback resend.")
                except discord.NotFound:
                    logger.warning(f"Old bot message (ID: {existing_bot_message_to_edit.id}) not found for deletion during edit fallback (already deleted?).")
                except discord.HTTPException as e_del:
                    logger.warning(f"Failed to delete old bot message (ID: {existing_bot_message_to_edit.id}) during edit fallback: {e_del}")

            else: # New response has audio, or is otherwise complex (e.g. new attachments).
                  # It's generally safer/easier to delete the old message and send a completely new one.
                logger.info(f"New response type (e.g., contains audio or new attachments) is not suitable for simple edit of message (ID: {existing_bot_message_to_edit.id}). Deleting and resending.")
                try:
                    await existing_bot_message_to_edit.delete()
                    logger.info(f"Deleted old bot message (ID: {existing_bot_message_to_edit.id}) due to response type change.")
                except discord.NotFound:
                    logger.warning(f"Old bot message (ID: {existing_bot_message_to_edit.id}) not found for deletion (already deleted?).")
                except discord.HTTPException as e:
                    logger.warning(f"Failed to delete old bot message (ID: {existing_bot_message_to_edit.id}) before sending new one: {e}")

        if can_try_native_voice:
            logger.info(f"Attempting to send native Discord voice message to {message_to_reply_to.author.name} in #{message_to_reply_to.channel}.")
            channel_id = str(message_to_reply_to.channel.id)

            try:
                with tempfile.NamedTemporaryFile(suffix='.ogg', delete=False) as temp_audio_file:
                    temp_audio_file.write(audio_data)
                    temp_ogg_file_path_for_upload = temp_audio_file.name
                logger.debug(f"Temporary OGG file for native voice upload: {temp_ogg_file_path_for_upload} ({len(audio_data)} bytes)")

                async with aiohttp.ClientSession() as session:
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
                                raise Exception(f"Invalid attachment slot response: {resp_slot_json}")
                        else:
                            raise Exception(f"Failed to get Discord upload slot: {resp_slot.status} - {await resp_slot.text()}")
                    logger.info(f"Obtained Discord upload slot. Upload URL: {attachment_metadata.get('upload_url')[:50]}...")

                    put_url = attachment_metadata["upload_url"]
                    with open(temp_ogg_file_path_for_upload, 'rb') as file_to_put:
                        put_headers = {'Content-Type': 'audio/ogg'}
                        async with session.put(put_url, data=file_to_put, headers=put_headers) as resp_put:
                            if resp_put.status != 200:
                                raise Exception(f"Failed to PUT audio to Discord CDN: {resp_put.status} - {await resp_put.text()}")
                    logger.info("Successfully PUT audio to Discord CDN.")

                    discord_cdn_filename = attachment_metadata["upload_filename"]
                    send_message_api_url = f"https://discord.com/api/v10/channels/{channel_id}/messages"
                    send_message_payload = {
                        "content": "",
                        "flags": 8192,
                        "attachments": [{"id": "0", "filename": "voice_message.ogg", "uploaded_filename": discord_cdn_filename, "duration_secs": round(duration_secs, 2), "waveform": waveform_b64}],
                        "message_reference": {"message_id": str(message_to_reply_to.id)},
                        "allowed_mentions": {"parse": [], "replied_user": False}
                    }
                    send_message_headers = {"Authorization": f"Bot {DISCORD_BOT_TOKEN}", "Content-Type": "application/json"}

                    async with session.post(send_message_api_url, json=send_message_payload, headers=send_message_headers) as resp_send:
                        if resp_send.status == 200 or resp_send.status == 201:
                            response_data = await resp_send.json()
                            message_id = response_data.get("id")
                            if message_id:
                                try:
                                    sent_message_obj = await message_to_reply_to.channel.fetch_message(message_id)
                                    logger.info(f"🎤 Successfully sent native Discord voice message (ID: {sent_message_obj.id}).")
                                    return sent_message_obj
                                except discord.HTTPException as fetch_err:
                                    logger.error(f"Native voice message sent but failed to fetch its object: {fetch_err}")
                            else:
                                logger.error("Native voice message sent, but no ID in response.")
                            return None # Sent, but couldn't get object
                        else:
                            raise Exception(f"Discord API send voice message failed: {resp_send.status} - {await resp_send.text()}")

            except Exception as e:
                logger.error(f"Error sending native Discord voice message: {e}. Falling back.", exc_info=True)
                if temp_ogg_file_path_for_upload and os.path.exists(temp_ogg_file_path_for_upload):
                    try:
                        discord_file = discord.File(temp_ogg_file_path_for_upload, "voice_response.ogg")
                        fallback_audio_msg = await message_to_reply_to.reply(file=discord_file)
                        logger.info(f"Sent voice as a generic .ogg file attachment (fallback, ID: {fallback_audio_msg.id}).")
                        if text_content and text_content.strip(): # Suppressed text, send now.
                             await MessageSender._send_text_reply(message_to_reply_to, text_content)
                        return fallback_audio_msg
                    except Exception as fallback_e:
                        logger.error(f"Fallback .ogg file send also failed: {fallback_e}. No audio sent.", exc_info=True)
                        if text_content and text_content.strip(): # Audio totally failed, text is the only option
                            return await MessageSender._send_text_reply(message_to_reply_to, text_content)
                elif text_content and text_content.strip(): # Audio failed before temp file, only text remains
                     return await MessageSender._send_text_reply(message_to_reply_to, text_content)
                return None # All fallbacks failed
            finally:
                if temp_ogg_file_path_for_upload and os.path.exists(temp_ogg_file_path_for_upload):
                    try:
                        os.unlink(temp_ogg_file_path_for_upload)
                        logger.debug(f"Deleted temporary OGG file: {temp_ogg_file_path_for_upload}")
                    except OSError as e_unlink:
                        logger.warning(f"Could not delete temp OGG file {temp_ogg_file_path_for_upload}: {e_unlink}")
            return None # Should have returned from try or except

        # Standard path: text and/or separate file attachment
        sent_text_message = None
        if text_content and text_content.strip():
            sent_text_message = await MessageSender._send_text_reply(message_to_reply_to, text_content)

        sent_audio_file_message = None
        if audio_data and not can_try_native_voice: # e.g. text was also present
            logger.info("Sending audio as a standard file attachment (text was also present or native voice not attempted).")
            temp_ogg_file_path_for_regular_upload = None
            try:
                with tempfile.NamedTemporaryFile(suffix='.ogg', delete=False) as temp_audio_file:
                    temp_audio_file.write(audio_data)
                    temp_ogg_file_path_for_regular_upload = temp_audio_file.name
                discord_file = discord.File(temp_ogg_file_path_for_regular_upload, "voice_response.ogg")
                if text_content and text_content.strip():
                    sent_audio_file_message = await message_to_reply_to.channel.send(file=discord_file)
                else:
                    sent_audio_file_message = await message_to_reply_to.reply(file=discord_file)
                logger.info(f"Sent voice as a generic .ogg file attachment (ID: {sent_audio_file_message.id}).")
            except Exception as e:
                logger.error(f"Failed to send .ogg file as attachment: {e}", exc_info=True)
            finally:
                if temp_ogg_file_path_for_regular_upload and os.path.exists(temp_ogg_file_path_for_regular_upload):
                    try: os.unlink(temp_ogg_file_path_for_regular_upload)
                    except OSError as e_unlink: logger.warning(f"Could not delete temp ogg file {temp_ogg_file_path_for_regular_upload}: {e_unlink}")

        # Prioritize returning the text message if sent, otherwise the audio file message.
        if sent_text_message:
            return sent_text_message
        elif sent_audio_file_message:
            return sent_audio_file_message
        return None


class AttachmentProcessor:
    """Downloads Discord attachments and uploads them to the Gemini File API."""

    @staticmethod
    async def _download_and_prepare_attachment(attachment: discord.Attachment) -> tuple[io.BytesIO, str, str] | None:
        """
        Downloads an attachment and determines its MIME type.

        Args:
            attachment: The discord.Attachment object.

        Returns:
            A tuple (BytesIO_object, mime_type, filename) or None if download fails.
        """
        try:
            logger.info(f"Downloading attachment: {attachment.filename} ({attachment.size} bytes) from {attachment.url}")
            async with aiohttp.ClientSession() as session:
                async with session.get(attachment.url) as response:
                    if response.status == 200:
                        data = await response.read()
                        # Use attachment.content_type if available and not generic, otherwise detect
                        mime_type = attachment.content_type
                        if not mime_type or mime_type == 'application/octet-stream':
                            detected_mime = MimeDetector.detect(data)
                            logger.info(f"Detected MIME for {attachment.filename}: {detected_mime} (Discord reported: {attachment.content_type})")
                            mime_type = detected_mime
                        else:
                             logger.info(f"Using Discord-provided MIME for {attachment.filename}: {mime_type}")
                        return io.BytesIO(data), mime_type, attachment.filename
                    else:
                        logger.warning(f"Failed to download {attachment.filename}: HTTP {response.status} - {await response.text()}")
                        return None
        except Exception as e:
            logger.error(f"Error downloading attachment {attachment.filename}: {e}", exc_info=True)
            return None

    @staticmethod
    async def _upload_to_file_api(file_like_object: io.BytesIO, mime_type: str, display_name: str) -> types.File | str:
        """
        Uploads a file-like object to the Gemini File API.

        Args:
            file_like_object: The io.BytesIO object containing file data.
            mime_type: The MIME type of the file.
            display_name: The display name for the file in Gemini.

        Returns:
            A Gemini `types.File` object if successful, or an error string.
        """
        global gemini_client
        if not gemini_client:
            logger.error("Gemini client not initialized. Cannot upload file to API.")
            return f"[Attachment: {display_name} - Gemini client not ready]"
        try:
            logger.info(f"📎 Uploading '{display_name}' (MIME: {mime_type}, Size: {file_like_object.getbuffer().nbytes} bytes) to Gemini File API...")
            # Ensure the file_like_object is at the beginning
            file_like_object.seek(0)
            uploaded_file = await gemini_client.aio.files.upload(
                file=file_like_object, # Pass the BytesIO object directly
                config=types.UploadFileConfig(mime_type=mime_type, display_name=display_name)
            )
            logger.info(f"✅ Gemini File API: Successfully uploaded '{display_name}' as '{uploaded_file.name}' (URI: {uploaded_file.uri}, MIME: {uploaded_file.mime_type})")
            return uploaded_file # This is a types.File object
        except Exception as e:
            logger.error(f"Error uploading '{display_name}' to Gemini File API: {e}", exc_info=True)
            return f"[Attachment: {display_name} - Gemini File API Upload failed. Error: {str(e)[:100]}]" # Return error string

    @staticmethod
    async def process_discord_attachments(attachments: list[discord.Attachment]) -> list[types.Part | str]:
        """
        Processes a list of Discord attachments, uploading them and creating Gemini Parts.

        Args:
            attachments: A list of `discord.Attachment` objects.

        Returns:
            A list of Gemini `types.Part` (containing FileData) or error strings.
        """
        parts = []
        if not attachments:
            return parts

        logger.info(f"Processing {len(attachments)} Discord attachment(s).")
        for attachment in attachments:
            prepared_data = await AttachmentProcessor._download_and_prepare_attachment(attachment)
            if prepared_data:
                file_io, mime, fname = prepared_data
                upload_result = await AttachmentProcessor._upload_to_file_api(file_io, mime, fname)

                if isinstance(upload_result, types.File):
                    # Create a Part using the FileData from the uploaded file's URI
                    parts.append(types.Part(file_data=types.FileData(mime_type=upload_result.mime_type, file_uri=upload_result.uri)))
                    logger.debug(f"Created Gemini Part for uploaded file: {upload_result.name}")
                else: # It's an error string
                    parts.append(upload_result) # Add the error string to parts
                    logger.warning(f"Failed to process attachment {fname} for Gemini Part, adding error string to prompt.")
            else:
                err_msg = f"[Attachment: {attachment.filename} - Download or preparation failed.]"
                parts.append(err_msg)
                logger.warning(err_msg)
        return parts


class ReplyChainProcessor:
    """Processes message reply chains to provide context to the LLM."""

    @staticmethod
    async def get_chain(message: discord.Message) -> list[dict]:
        """
        Fetches the reply chain for a given message, up to `Config.MAX_REPLY_DEPTH`.

        Args:
            message: The starting discord.Message.

        Returns:
            A list of dictionaries, each representing a message in the chain (oldest first).
        """
        chain = []
        current_msg_obj = message
        depth = 0
        logger.debug(f"Fetching reply chain for message ID {message.id}, max depth {Config.MAX_REPLY_DEPTH}.")
        while current_msg_obj and depth < Config.MAX_REPLY_DEPTH:
            msg_info = {
                'message_obj': current_msg_obj, # Keep the object for potential attachment processing
                'author_name': f"{current_msg_obj.author.display_name} (@{current_msg_obj.author.name})",
                'author_id': current_msg_obj.author.id,
                'is_bot': current_msg_obj.author.bot,
                'content': current_msg_obj.content,
                'attachments': list(current_msg_obj.attachments), # Store attachments for later processing
            }
            chain.insert(0, msg_info) # Insert at the beginning to maintain oldest-first order

            if hasattr(current_msg_obj, 'reference') and current_msg_obj.reference and current_msg_obj.reference.message_id:
                try:
                    # Fetch the referenced message
                    referenced_message = await current_msg_obj.channel.fetch_message(current_msg_obj.reference.message_id)
                    current_msg_obj = referenced_message
                    depth += 1
                except (discord.NotFound, discord.Forbidden) as e:
                    logger.warning(f"Could not fetch referenced message {current_msg_obj.reference.message_id}: {e}")
                    break # Stop if a message in the chain is inaccessible
                except Exception as e_fetch:
                    logger.error(f"Unexpected error fetching referenced message: {e_fetch}", exc_info=True)
                    break
            else:
                break # No more references, end of chain
        logger.info(f"Fetched reply chain of depth {len(chain)} for message ID {message.id}.")
        return chain

    @staticmethod
    def format_context_for_llm(chain: list[dict], current_message_id: int) -> str:
        """
        Formats the message chain (excluding the current message) as a textual context for the LLM.

        Args:
            chain: The message chain (list of dicts from `get_chain`).
            current_message_id: The ID of the current message being processed (to exclude it from context).

        Returns:
            A string representing the reply chain context, or an empty string if no relevant context.
        """
        if len(chain) <= 1: # Only the current message or no chain
            return ""

        context_str = "\n--- START OF REPLY CHAIN CONTEXT (Oldest to Newest) ---\n"
        for msg_data in chain:
            if msg_data['message_obj'].id == current_message_id:
                # This is the current message itself, which will be handled separately.
                # We only want context from messages *before* it in the chain.
                continue

            role = "User"
            if msg_data['is_bot']:
                role = "Assistant (You)" if msg_data['author_id'] == bot.user.id else "Assistant (Other Bot)"

            context_str += f"{role} ({msg_data['author_name']}): {msg_data['content']}"
            if msg_data['attachments']:
                # For textual context, just note that attachments were present.
                # Actual attachment processing for Gemini happens elsewhere if they are from the *directly replied-to* message.
                attachment_desc = ", ".join([f"{att.filename} ({att.content_type or 'unknown type'})" for att in msg_data['attachments']])
                context_str += f" [Attachments noted: {attachment_desc}]"
            context_str += "\n"

        context_str += "--- END OF REPLY CHAIN CONTEXT ---\n\n"
        logger.debug(f"Formatted reply chain context (length: {len(context_str)} chars).")
        return context_str


class ChatSessionManager:
    """Manages Gemini chat sessions for users/guilds."""

    @staticmethod
    async def get_session(guild_id: int, user_id: int, user: discord.User, channel: discord.abc.Messageable) -> GenAIChatSession | None:
        """
        Retrieves or creates a Gemini chat session.

        Args:
            guild_id: The ID of the guild (0 for DMs).
            user_id: The ID of the user.
            user: The discord.User object.
            channel: The discord.abc.Messageable channel object.

        Returns:
            A Gemini `types.ChatSession` object, or None if client not ready.
        """
        global gemini_client
        if not gemini_client:
            logger.error("Gemini client not initialized. Cannot get or create chat session.")
            return None

        session_key = f"{guild_id}_{user_id}"
        if session_key not in chat_sessions:
            logger.info(f"Creating new Gemini chat session for user '{user.name}' (ID: {user_id}) in guild {guild_id} (Session Key: {session_key})")
            gemini_generation_config = GeminiConfigManager.create_config(user, channel)
            # Initialize chat with model, config, and empty history
            chat_sessions[session_key] = gemini_client.aio.chats.create(
                model=Config.MODEL_ID,
                config=gemini_generation_config,
                history=[] # Start with an empty history for new sessions
            )
            logger.info(f"New chat session created for {session_key} with model {Config.MODEL_ID}.")
        else:
            logger.debug(f"Reusing existing chat session for {session_key}.")
        return chat_sessions[session_key]

    @staticmethod
    def clear_guild_sessions(guild_id: int) -> int:
        """Clears all chat sessions associated with a specific guild."""
        keys_to_remove = [k for k in chat_sessions.keys() if k.startswith(f"{guild_id}_")]
        cleared_count = 0
        for k in keys_to_remove:
            if chat_sessions.pop(k, None):
                cleared_count += 1
        if cleared_count > 0:
            logger.info(f"🧹 Cleared {cleared_count} chat session(s) for guild {guild_id}.")
        else:
            logger.info(f"No active chat sessions found to clear for guild {guild_id}.")
        return cleared_count

    @staticmethod
    def clear_dm_session(user_id: int) -> bool:
        """Clears the DM chat session for a specific user."""
        session_key = f"0_{user_id}" # Guild ID is 0 for DMs
        if chat_sessions.pop(session_key, None):
            logger.info(f"🧹 Cleared DM chat session for user {user_id}.")
            return True
        logger.info(f"No active DM chat session found to clear for user {user_id}.")
        return False

class MessageProcessor:
    """Core class for processing incoming Discord messages and interacting with Gemini."""
    SPEAK_TAG_PATTERN = re.compile(r"\[SPEAK(?::([A-Z_]+))?\]\s*(.*)", re.IGNORECASE | re.DOTALL)

    @staticmethod
    async def _build_message_parts(message: discord.Message, cleaned_content: str, reply_chain_data: list[dict]) -> list[types.Part | str]:
        """
        Constructs the list of parts (text, files, YouTube links) to send to Gemini.
        This includes handling reply chains and attachments.

        Args:
            message: The current discord.Message being processed.
            cleaned_content: The text content of the current message, with mentions stripped.
            reply_chain_data: The processed reply chain data.

        Returns:
            A list of Gemini `types.Part` objects or string error messages.
        """
        parts = []
        logger.debug(f"Building message parts for Gemini. Current message content (cleaned): '{cleaned_content[:100]}...'")

        # 1. Add textual context from the reply chain (if any)
        # This context excludes the current message itself.
        if reply_chain_data:
            textual_reply_context = ReplyChainProcessor.format_context_for_llm(reply_chain_data, message.id)
            if textual_reply_context.strip():
                parts.append(textual_reply_context)
                logger.info("🔗 Added textual reply chain context to Gemini prompt.")

        # 2. Process attachments from the *directly replied-to message* (if it's not from the bot itself)
        # The `reply_chain_data` is ordered [oldest, ..., message_replied_to, current_message]
        # So, if len > 1, `reply_chain_data[-2]` is the message directly replied to.
        if message.reference and message.reference.message_id and len(reply_chain_data) > 1:
            replied_to_msg_data = reply_chain_data[-2] # The message directly replied to
            if replied_to_msg_data['author_id'] != bot.user.id and replied_to_msg_data['attachments']:
                logger.info(f"📎 Processing {len(replied_to_msg_data['attachments'])} attachment(s) from replied-to message by '{replied_to_msg_data['author_name']}'.")
                replied_attachments_parts = await AttachmentProcessor.process_discord_attachments(replied_to_msg_data['attachments'])
                if replied_attachments_parts:
                    parts.extend(p for p in replied_attachments_parts if p) # Add only non-empty/successful parts
                    logger.debug(f"Added {len(replied_attachments_parts)} parts from replied-to message attachments.")

        # 3. Process YouTube links from the current message's cleaned content
        content_after_youtube, youtube_file_data_parts = YouTubeProcessor.process_content(cleaned_content)
        if youtube_file_data_parts:
            parts.extend(youtube_file_data_parts)
            logger.info(f"Added {len(youtube_file_data_parts)} YouTube FileData parts to Gemini prompt.")
        processed_content_for_text_part = content_after_youtube # Use content after YT URLs are removed for the main text part

        # 4. Add the textual content of the current message (after YouTube processing)
        if processed_content_for_text_part.strip():
            # Clearly demarcate the user's current message text
            parts.append(f"User's current message: {processed_content_for_text_part.strip()}")
            logger.debug(f"Added current message text part: \"{processed_content_for_text_part.strip()[:100]}...\"")


        # 5. Process attachments from the current message
        if message.attachments:
            logger.info(f"📎 Processing {len(message.attachments)} attachment(s) from the current message.")
            current_message_attachment_parts = await AttachmentProcessor.process_discord_attachments(list(message.attachments))
            if current_message_attachment_parts:
                parts.extend(p for p in current_message_attachment_parts if p)
                logger.debug(f"Added {len(current_message_attachment_parts)} parts from current message attachments.")

        # Final filtering and fallback
        # Remove None entries or parts that are just error strings but should not block if other content exists.
        # However, error strings from upload failures *should* be passed to the LLM.
        final_parts = [p for p in parts if p] # Filter out None from failed uploads if they return None
        # Filter out parts that are strings but completely empty or whitespace
        final_parts = [p for p in final_parts if not (isinstance(p, str) and not p.strip())]


        if not final_parts:
            # This case means no text, no successful attachments, no YT links.
            # e.g., user sent only a mention, or attachments that all failed to process.
            fallback_text = "User sent a message that could not be fully processed for content (e.g., only mentions, or all attachments failed)."
            if cleaned_content.strip(): # If there was some text initially after mention stripping
                fallback_text = f"User's message (partially processed): {cleaned_content.strip()}"
            elif message.attachments:
                fallback_text = "User sent attachments, but they could not be processed for the AI prompt."
            else: # Truly empty or only a mention
                fallback_text = "User sent an empty message or only a mention."

            final_parts.append(fallback_text)
            logger.warning(f"No substantive parts were built for Gemini. Added fallback part: \"{fallback_text}\"")

        logger.info(f"Final assembled parts for Gemini (count: {len(final_parts)}):")
        for i, part_item in enumerate(final_parts):
            if isinstance(part_item, str):
                logger.info(f"  Part {i+1} [Text]: \"{part_item[:150]}...\"")
            elif hasattr(part_item, 'file_data') and part_item.file_data:
                logger.info(f"  Part {i+1} [FileData]: URI='{part_item.file_data.file_uri}', MIME='{part_item.file_data.mime_type}'")
            else:
                logger.info(f"  Part {i+1} [Unknown Part Type]: {str(part_item)[:150]}...")
        return final_parts


    @staticmethod
    async def process(message: discord.Message, bot_message_to_edit: discord.Message | None = None):
        """
        Main processing logic for an incoming Discord message.
        Fetches context, interacts with Gemini, and sends a response.
        """
        # Strip mentions of the bot itself from the content for LLM processing
        content_for_llm = re.sub(r'<[@#&!][^>]+>', '', message.content).strip()
        logger.info(f"Processing message from {message.author.name} (ID: {message.author.id}, Message ID: {message.id}). Original content: \"{message.content[:100]}...\". Cleaned for LLM: \"{content_for_llm[:100]}...\"")

        reset_command_str = f"{bot.command_prefix}reset"
        if message.content.strip().lower().startswith(reset_command_str):
            guild_id = message.guild.id if message.guild else 0
            user_id_for_reset = message.author.id

            if guild_id != 0:
                count = ChatSessionManager.clear_guild_sessions(guild_id)
                response_text = f"🧹 Cleared all {count} chat session(s) for this server ({message.guild.name})!"
                logger.info(f"Reset command executed by {message.author.name} in guild {guild_id}. Cleared {count} sessions.")
            else:
                if ChatSessionManager.clear_dm_session(user_id_for_reset):
                    response_text = "🧹 Your DM chat history with me has been reset!"
                    logger.info(f"Reset command executed by {message.author.name} in DMs. Session cleared.")
                else:
                    response_text = "You don't have an active DM chat history with me to reset."
                    logger.info(f"Reset command by {message.author.name} in DMs, but no session found.")
            bot_response_message = await MessageSender.send(
                message,
                response_text,
                audio_data=None,
                existing_bot_message_to_edit=None # Explicitly None for reset responses
            )
            if bot_response_message:
                active_bot_responses[message.id] = bot_response_message
            return

        async with message.channel.typing():
            try:
                guild_id = message.guild.id if message.guild else 0
                chat_session = await ChatSessionManager.get_session(
                    guild_id, message.author.id, message.author, message.channel
                )
                if not chat_session:
                     bot_response_message = await MessageSender.send(message, "Sorry, I couldn't establish a chat session with the AI at the moment.", None)
                     if bot_response_message:
                         active_bot_responses[message.id] = bot_response_message
                     return

                reply_chain_data = await ReplyChainProcessor.get_chain(message)
                gemini_parts_for_prompt = await MessageProcessor._build_message_parts(message, content_for_llm, reply_chain_data)

                is_meaningfully_empty = True
                if not gemini_parts_for_prompt:
                    is_meaningfully_empty = True
                else:
                    has_substantive_content = False
                    for part_item in gemini_parts_for_prompt:
                        if isinstance(part_item, str) and part_item.strip() and not part_item.startswith("[Attachment:"):
                            if not (part_item.startswith("User's current message:") and not part_item.replace("User's current message:", "").strip()):
                                has_substantive_content = True
                                break
                        elif not isinstance(part_item, str): # e.g. types.Part with FileData
                            has_substantive_content = True
                            break
                    is_meaningfully_empty = not has_substantive_content


                if is_meaningfully_empty:
                    logger.info("Message content was meaningfully empty after processing. Sending a default greeting.")
                    bot_response_message = await MessageSender.send(
                        message,
                        "Hello! How can I help you today? (It seems your message was empty or only contained mentions I stripped).",
                        None,
                        existing_bot_message_to_edit=bot_message_to_edit
                    )
                    if bot_response_message:
                        active_bot_responses[message.id] = bot_response_message
                    return

                logger.info(f"💬 Sending {len(gemini_parts_for_prompt)} parts to Gemini chat session for user '{message.author.name}'. Model: {Config.MODEL_ID}")
                if hasattr(chat_session, '_config') and chat_session._config and chat_session._config.system_instruction:
                    logger.debug(f"System Instruction for this session (first 200 chars): {str(chat_session._config.system_instruction)[:200]}...")
                else:
                    logger.debug("No system instruction found in chat session config (or config itself is None).")

                response_from_gemini = await chat_session.send_message(gemini_parts_for_prompt)
                logger.debug(f"Received response from Gemini for user '{message.author.name}'.")

                response_text = ResponseExtractor.extract_text(response_from_gemini)
                logger.info(f"Extracted text from Gemini response: \"{response_text[:150]}...\"")

                final_text_for_discord = response_text
                ogg_audio_data = None
                audio_duration = 0.0
                audio_waveform_b64 = Config.DEFAULT_WAVEFORM_PLACEHOLDER

                speak_match = MessageProcessor.SPEAK_TAG_PATTERN.match(response_text)
                if speak_match:
                    style, text_after_tag = speak_match.groups()
                    text_for_tts_generation = text_after_tag.strip()
                    logger.info(f"🎤 [SPEAK] tag detected. Style: '{style or 'default'}'. Text for TTS: \"{text_for_tts_generation[:100]}...\"")

                    final_text_for_discord = text_for_tts_generation

                    if not text_for_tts_generation:
                        logger.warning("Empty text after [SPEAK] tag. No TTS will be generated. Sending original response text (if any).")
                        if response_text.strip() == speak_match.group(0).strip():
                             final_text_for_discord = ""
                        else:
                             final_text_for_discord = response_text.replace(speak_match.group(0), "").strip()
                    else:
                        tts_prompt_for_generator = f"In a {style.replace('_', ' ').lower()} tone, say: {text_for_tts_generation}" if style else text_for_tts_generation
                        tts_result = await TTSGenerator.generate_speech_ogg(tts_prompt_for_generator)
                        if tts_result:
                            ogg_audio_data, audio_duration, audio_waveform_b64 = tts_result
                            logger.info(f"TTS generation successful. Audio duration: {audio_duration:.2f}s.")
                            final_text_for_discord = None # Text is handled by audio
                        else:
                            logger.warning(f"🎤 OGG TTS generation failed for: \"{tts_prompt_for_generator[:100]}...\". Sending text only.")
                else:
                    logger.debug("No [SPEAK] tag found in Gemini response.")

                new_or_edited_bot_message = await MessageSender.send(
                    message_to_reply_to=message,
                    text_content=final_text_for_discord,
                    audio_data=ogg_audio_data,
                    duration_secs=audio_duration,
                    waveform_b64=audio_waveform_b64,
                    existing_bot_message_to_edit=bot_message_to_edit
                )

                if new_or_edited_bot_message:
                    active_bot_responses[message.id] = new_or_edited_bot_message
                    logger.info(f"Stored/updated bot response (ID: {new_or_edited_bot_message.id}) for user message (ID: {message.id})")
                else:
                    active_bot_responses.pop(message.id, None) # Ensure no stale entry if send/edit failed
                    logger.warning(f"MessageSender did not return a message object for user message (ID: {message.id}). Cannot track for edits.")


            except Exception as e:
                logger.error(f"Message processing pipeline error for user {message.author.name}: {e}", exc_info=True)
                error_reply_msg = await MessageSender.send(
                    message,
                    "❌ I encountered an unexpected error while processing your request. The developers have been notified.",
                    None,
                    existing_bot_message_to_edit=bot_message_to_edit
                )
                if error_reply_msg:
                    active_bot_responses[message.id] = error_reply_msg
                else:
                    active_bot_responses.pop(message.id, None) # Ensure no stale entry


# --- Discord Event Handlers ---
@bot.event
async def on_ready():
    """Called when the bot is successfully connected to Discord."""
    logger.info(f"🎉 Logged in as {bot.user.name} (ID: {bot.user.id})")
    logger.info(f"🔗 Discord.py Version: {discord.__version__}")
    logger.info(f"🤖 Bot is ready and online!")
    logger.info(f"🧠 Using Main Gemini Model: {Config.MODEL_ID}")
    logger.info(f"🗣️ Using TTS Gemini Model: {Config.TTS_MODEL_ID} with Voice: {Config.VOICE_NAME}")
    logger.info(f"🔧 Command Prefix: {bot.command_prefix}")
    try:
        await bot.change_presence(activity=discord.Activity(type=discord.ActivityType.listening, name=f"messages | {bot.command_prefix}reset"))
        logger.info("Bot presence updated successfully.")
    except Exception as e:
        logger.warning(f"Could not set bot presence: {e}")


@bot.event
async def on_message(message: discord.Message):
    """Called when a message is sent in a channel the bot can see."""
    if message.author == bot.user or message.author.bot:
        return

    is_dm = isinstance(message.channel, discord.DMChannel)
    is_mentioned = bot.user.mentioned_in(message)
    is_reply_to_bot = False
    if message.reference and message.reference.message_id:
        try:
            referenced_message = await message.channel.fetch_message(message.reference.message_id)
            if referenced_message.author == bot.user:
                is_reply_to_bot = True
        except (discord.NotFound, discord.Forbidden):
            logger.debug(f"Could not fetch referenced message {message.reference.message_id} to check author.")
        except Exception as e_ref:
            logger.warning(f"Error fetching referenced message for on_message: {e_ref}", exc_info=True)

    is_reset_command = message.content.lower().startswith(f"{bot.command_prefix}reset")

    should_process = False
    if is_dm or is_mentioned or is_reply_to_bot:
        should_process = True
        logger.info(f"Processing message (ID: {message.id}) due to DM/Mention/Reply. Author: {message.author.name}, Channel: #{message.channel}")
    elif is_reset_command:
        should_process = True
        logger.info(f"Processing '{bot.command_prefix}reset' command (Message ID: {message.id}) from {message.author.name} in #{message.channel}")

    if should_process:
        await MessageProcessor.process(message)
    else:
        logger.debug(f"Ignoring message (ID: {message.id}) from {message.author.name} in #{message.channel} (not DM, not mentioned, not reply to bot, not reset command): \"{message.content[:50]}...\"")

@bot.event
async def on_message_edit(before: discord.Message, after: discord.Message):
    """Called when a message is edited by a user."""
    if after.author == bot.user or after.author.bot:
        return

    # Determine if the *edited* message ('after') should trigger processing
    is_dm_after = isinstance(after.channel, discord.DMChannel)
    is_mentioned_after = bot.user.mentioned_in(after)
    is_reply_to_bot_after = False
    if after.reference and after.reference.message_id:
        try:
            referenced_message_after = await after.channel.fetch_message(after.reference.message_id)
            if referenced_message_after.author == bot.user:
                is_reply_to_bot_after = True
        except (discord.NotFound, discord.Forbidden):
            logger.debug(f"Referenced message for 'after' state (ID: {after.reference.message_id if after.reference else 'None'}) not found or forbidden in on_message_edit.")
        except Exception as e_ref:
            logger.warning(f"Error fetching referenced message for 'after' state in on_message_edit: {e_ref}", exc_info=True)

    is_reset_command_after = after.content.lower().startswith(f"{bot.command_prefix}reset")
    should_process_after = is_dm_after or is_mentioned_after or is_reply_to_bot_after or is_reset_command_after

    if should_process_after:
        logger.info(f"Edited message (ID: {after.id}) by {after.author.name} now qualifies for processing.")
        logger.debug(f"Before content (ID: {before.id}): \"{before.content[:100]}...\" After content (ID: {after.id}): \"{after.content[:100]}...\"")

        existing_bot_response_object = active_bot_responses.pop(before.id, None) # Try to get the bot's message to edit
                                                                                 # We pop it here; MessageProcessor will re-add the new/edited one

        await MessageProcessor.process(after, bot_message_to_edit=existing_bot_response_object)

    else: # Edited to no longer qualify for processing
        # If there was an old response, delete it as the message is no longer targeted at the bot.
        if before.id in active_bot_responses:
            bot_response_to_delete = active_bot_responses.pop(before.id, None)
            if bot_response_to_delete:
                try:
                    await bot_response_to_delete.delete()
                    logger.info(f"Deleted previous bot response (ID: {bot_response_to_delete.id}) because user message (ID: {before.id}) was edited to no longer qualify.")
                except discord.NotFound:
                     logger.warning(f"Previous bot response for message {before.id} (edited to no longer qualify) not found (already deleted).")
                except discord.HTTPException as e:
                     logger.error(f"Error deleting previous bot response for message {before.id} (edited to no longer qualify): {e}")
        logger.debug(f"Message (ID: {after.id}) by {after.author.name} was edited, but the new content does not qualify for processing. Any prior response handled.")


@bot.event
async def on_message_delete(message: discord.Message):
    """Called when a message is deleted."""
    if message.id in active_bot_responses:
        bot_response_to_delete = active_bot_responses.pop(message.id, None)
        if bot_response_to_delete:
            try:
                await bot_response_to_delete.delete()
                logger.info(f"Bot response (ID: {bot_response_to_delete.id}) deleted because original user message (ID: {message.id}) was deleted.")
            except discord.NotFound:
                logger.warning(f"Bot response (ID: {bot_response_to_delete.id}) for deleted message (ID: {message.id}) not found (already deleted).")
            except discord.HTTPException as e:
                logger.error(f"Error deleting bot response (ID: {bot_response_to_delete.id}) for deleted message (ID: {message.id}): {e}")
        else:
            logger.warning(f"Message {message.id} was in active_bot_responses keys upon deletion, but pop returned None.")


# --- Setup and Main Execution ---
def validate_environment_variables():
    """Validates that required environment variables are set."""
    if not DISCORD_BOT_TOKEN:
        msg = "❌ CRITICAL: DISCORD_BOT_TOKEN environment variable not found. The bot cannot start."
        logger.critical(msg)
        raise ValueError(msg)
    if not GEMINI_API_KEY:
        msg = "❌ CRITICAL: GOOGLE_AI_KEY environment variable not found. The bot cannot start."
        logger.critical(msg)
        raise ValueError(msg)
    logger.info("✅ Environment variables (DISCORD_BOT_TOKEN, GOOGLE_AI_KEY) validated.")

def setup_logging():
    """Configures logging for the application."""
    logging.basicConfig(
        level=logging.WARNING,
        format='%(asctime)s [%(levelname)s] [%(name)s] %(message)s',
        handlers=[logging.StreamHandler()],
        force=True
    )

    app_logger = logging.getLogger("Bard")
    app_logger.setLevel(logging.INFO)
    app_logger.propagate = False

    console_handler = logging.StreamHandler()
    file_handler = logging.FileHandler('bard.log', mode='a', encoding='utf-8')

    formatter = logging.Formatter('%(asctime)s [%(levelname)s] [%(name)s:%(module)s:%(funcName)s:%(lineno)d] %(message)s')
    console_handler.setFormatter(formatter)
    file_handler.setFormatter(formatter)

    app_logger.addHandler(console_handler)
    app_logger.addHandler(file_handler)

    logging.getLogger("discord").setLevel(logging.WARNING)
    logging.getLogger("discord.http").setLevel(logging.WARNING)
    logging.getLogger("google.genai").setLevel(logging.WARNING)

    app_logger.info("📝 Logging configured for Bard. Outputting to console and bard.log")
    app_logger.info("This is a DEMO info message from the Bard logger.")


def main():
    """Main function to set up and run the bot."""
    global gemini_client
    try:
        setup_logging()
        logger.info("🚀 Initializing Gemini Discord Bot...")
        validate_environment_variables()

        gemini_client = genai.Client(
            api_key=GEMINI_API_KEY,
            http_options={'api_version': 'v1beta'}
        )
        logger.info(f"🤖 Gemini AI Client initialized. Using API version: v1beta")

        logger.info(f"🔑 Discord Token: {'*' * (len(DISCORD_BOT_TOKEN) - 4) + DISCORD_BOT_TOKEN[-4:] if DISCORD_BOT_TOKEN else 'Not Set'}")
        logger.info(f"🔑 Gemini API Key: {'*' * (len(GEMINI_API_KEY) - 4) + GEMINI_API_KEY[-4:] if GEMINI_API_KEY else 'Not Set'}")

        logger.info("📡 Starting Discord bot...")
        bot.run(DISCORD_BOT_TOKEN, log_handler=None)

    except ValueError as ve:
        print(f"Configuration Error: {ve}")
        return 1
    except discord.LoginFailure:
        logger.critical("❌ Discord Login Failed: Improper token provided. Please check your DISCORD_BOT_TOKEN.")
        print("❌ Discord Login Failed: Improper token provided. Please check your DISCORD_BOT_TOKEN.")
        return 1
    except Exception as e:
        logger.critical(f"💥 A fatal error occurred during bot startup or runtime: {e}", exc_info=True)
        print(f"💥 A fatal error occurred: {e}")
        return 1
    finally:
        logger.info("🛑 Bot shutdown sequence initiated.")

if __name__ == "__main__":
    exit_code = main()
    if exit_code == 0:
        logger.info("Bot exited gracefully.")
    else:
        logger.warning(f"Bot exited with code {exit_code}.")
    logging.shutdown()