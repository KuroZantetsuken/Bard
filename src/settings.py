import logging
import os

from dotenv import find_dotenv, load_dotenv

log = logging.getLogger("Bard")


load_dotenv(find_dotenv())


class Settings:
    """
    Manages all application-wide configuration settings and environment variables.
    Loads settings from env file and provides access to various constants.
    """

    # --- Core Bot Settings ---
    # The Discord bot token, retrieved from environment variables.
    DISCORD_BOT_TOKEN = os.getenv("DISCORD_BOT_TOKEN")
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
    PRESENCE_TYPE = "custom"
    # The text to display for the presence.
    PRESENCE_TEXT = "gooning"
    # The emoji for "custom" statuses (optional).
    PRESENCE_EMOJI = "❓"

    # --- Gemini AI Model Settings ---
    # The API key for the Gemini AI, retrieved from environment variables.
    GEMINI_API_KEY = os.getenv("GEMINI_API_KEY")
    # Optional custom base URL for the Gemini API.
    GEMINI_BASE_URL = os.getenv("GEMINI_BASE_URL")
    # The primary model identifier for text generation.
    MODEL_ID = "gemini-2.5-flash"
    # The secondary model identifier for simple sub-tasks.
    MODEL_ID_SECONDARY = "gemini-2.5-flash-lite"
    # The specific model identifier for text-to-speech generation.
    MODEL_ID_TTS = "gemini-2.5-flash-preview-tts"
    # The specific model identifier for image generation.
    MODEL_ID_IMAGE_GENERATION = "gemini-2.5-flash-image"
    # The pre-built voice to use for text-to-speech.
    VOICE_NAME = "Kore"

    # --- AI Interaction and Limits ---
    # The maximum depth for fetching reply chains in Discord.
    MAX_REPLY_DEPTH = 10
    # The token budget for Gemini's internal "thinking" process when using tools.
    # Note: For Gemini 3 models, this is legacy. Use THINKING_LEVEL instead.
    THINKING_BUDGET = 128
    # The thinking level for Gemini 3 models ("low" or "high").
    THINKING_LEVEL = "low"
    # The maximum number of tokens for a generated response from Gemini.
    MAX_OUTPUT_TOKENS = 65536
    # A global timeout in seconds for external tool execution.
    TOOL_TIMEOUT_SECONDS = 30
    # The maximum number of long-term memories to store per user. 0 disables this check.
    MAX_MEMORIES = 32

    # --- File and Path Settings ---
    # The directory to the FFmpeg executable.
    FFMPEG_PATH = "ffmpeg"
    # The directory to the yt-dlp executable.
    YTDLP_PATH = "yt-dlp"
    # The directory where prompt templates (.prompt.md) are stored.
    PROMPT_DIR = "data/prompts/"
    # The directory where log files are saved.
    LOG_DIR = "data/logs/"
    # The directory for storing long-term user memories (.memory.json).
    MEMORY_DIR = "data/memories/"
    # The directory where scraped content is cached.
    CACHE_DIR = "data/cache/"
    # The directory containing Playwright browser extensions
    PLAYWRIGHT_EXTENSIONS_PATH = "data/extensions/"
    # The directory containing persistent browser data
    PLAYWRIGHT_BROWSER_PATH = "data/browser/"
    # The directory to the DiscordChatExporter.Cli executable.
    DISCORD_CHAT_EXPORTER_PATH = os.path.expanduser(
        "~/DiscordChatExporter.Cli.linux-x64/DiscordChatExporter.Cli"
    )

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

    # --- Debugging & Testing ---
    # A list of bot user IDs that are allowed to trigger the bot (e.g., for testing).
    ALLOWED_BOT_IDS = [
        int(id.strip())
        for id in os.getenv("ALLOWED_BOT_IDS", "").split(",")
        if id.strip()
    ]

    @classmethod
    def validate_settings(cls):
        """
        Loads environment variables and validates their presence.
        """
        if not cls.DISCORD_BOT_TOKEN:
            raise ValueError("DISCORD_BOT_TOKEN is not set.")
        if not cls.GEMINI_API_KEY:
            raise ValueError("GEMINI_API_KEY is not set.")
