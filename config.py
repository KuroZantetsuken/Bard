import logging
import os

from dotenv import load_dotenv

# Initialize logger for configuration messages.
logger = logging.getLogger("Bard")


class Config:
    """
    Manages all application-wide configuration settings and environment variables.
    Loads settings from .env file and provides access to various constants.
    """

    # --- Core Bot Settings ---
    # The Discord bot token, retrieved from environment variables.
    DISCORD_BOT_TOKEN = os.getenv("DISCORD_BOT_TOKEN")
    # The command prefix for bot commands (e.g., !reset).
    COMMAND_PREFIX = "!"
    # The emoji used to trigger a re-run of a prompt.
    RETRY_EMOJI = "🔄"
    # The emoji used to cancel a response generation.
    CANCEL_EMOJI = "🚫"
    # Maximum allowed characters per message in Discord.
    MAX_DISCORD_MESSAGE_LENGTH = 2000
    # Discord message flag to suppress embeds and indicate a voice message.
    DISCORD_VOICE_MESSAGE_FLAG = 8192

    # --- Bot Presence Settings ---
    # The type of activity for the bot's presence.
    # Options: "playing", "listening", "watching", "custom"
    PRESENCE_TYPE = "listening"
    # The text to display for the presence.
    PRESENCE_TEXT = "your questions"
    # The emoji for "custom" statuses (optional).
    PRESENCE_EMOJI = "❓"

    # --- Gemini AI Model Settings ---
    # The API key for the Gemini AI, retrieved from environment variables.
    GEMINI_API_KEY = os.getenv("GEMINI_API_KEY")
    # The primary model identifier for text generation.
    MODEL_ID = "gemini-2.5-flash"
    # The secondary model identifier for simple sub-tasks.
    MODEL_ID_SECONDARY = "gemini-2.5-flash-lite"
    # The specific model identifier for text-to-speech generation.
    MODEL_ID_TTS = "gemini-2.5-flash-preview-tts"
    # The specific model identifier for image generation.
    MODEL_ID_IMAGE_GENERATION = "gemini-2.5-flash-image-preview"
    # The pre-built voice to use for text-to-speech.
    VOICE_NAME = "Kore"

    # --- AI Interaction and Limits ---
    # The maximum depth for fetching reply chains in Discord.
    MAX_REPLY_DEPTH = 10
    # The token budget for Gemini's internal "thinking" process when using tools.
    THINKING_BUDGET = -1
    # The maximum number of tokens for a generated response from Gemini.
    MAX_OUTPUT_TOKENS = 65536
    # A global timeout in seconds for external tool execution.
    TOOL_TIMEOUT_SECONDS = 30
    # The maximum number of estimated tokens for video content. If a video's estimated token
    # cost exceeds this limit, only its audio track and metadata will be sent to the model.
    # Otherwise, the full video content (visuals and audio) will be sent.
    MAX_VIDEO_TOKENS_FOR_FULL_PROCESSING = 10000
    # Estimated token cost per second for video content (visual data).
    VIDEO_TOKEN_COST_PER_SECOND = 258
    # Estimated token cost per second for audio content (speech data).
    AUDIO_TOKEN_COST_PER_SECOND = 32

    # --- History and Memory Settings ---
    # The maximum number of conversational turns (user + assistant) to keep in short-term history.
    MAX_HISTORY_TURNS = 5
    # The maximum age (in minutes) for a turn to be considered for history. 0 disables this check.
    MAX_HISTORY_AGE = 10
    # The maximum number of long-term memories to store per user. 0 disables this check.
    MAX_MEMORIES = 32

    # --- File and Path Settings ---
    # The path to the FFmpeg executable.
    FFMPEG_PATH = "ffmpeg"
    # The path to the yt-dlp executable.
    YTDLP_PATH = "yt-dlp"
    # The directory where prompt templates (.prompt.md) are stored.
    PROMPT_DIR = "prompts"
    # The directory where log files are saved.
    LOG_DIR = "logs"
    # The directory for storing short-term chat history (.history.json).
    HISTORY_DIR = "history"
    # The directory for storing long-term user memories (.memory.json).
    MEMORY_DIR = "memories"
    # The directory where tool modules are located.
    TOOLS_DIR = "bard/tools"

    # --- Logging Configuration ---
    # Enable or disable logging to the console.
    LOG_CONSOLE_ENABLED = True
    # The minimum level for console logs (DEBUG, INFO, WARNING, ERROR, CRITICAL).
    LOG_CONSOLE_LEVEL = "INFO"
    # Enable or disable logging to a file.
    LOG_FILE_ENABLED = True
    # The minimum level for file logs (DEBUG, INFO, WARNING, ERROR, CRITICAL).
    LOG_FILE_LEVEL = "DEBUG"
    # The maximum age in days for log files before they are pruned. 0 disables this check.
    LOG_FILE_MAX_AGE_DAYS = 7
    # The maximum number of log files to keep. 0 disables pruning.
    LOG_FILE_MAX_COUNT = 10
    # Enable or disable automatic log pruning on application startup.
    LOG_PRUNE_ON_STARTUP = True

    @classmethod
    def load_and_validate(cls):
        """
        Loads environment variables and validates their presence.
        This method should be called explicitly after logging is configured.
        """
        load_dotenv()
        if not cls.DISCORD_BOT_TOKEN:
            raise ValueError("DISCORD_BOT_TOKEN is not set.")
        if not cls.GEMINI_API_KEY:
            raise ValueError("GEMINI_API_KEY is not set.")
