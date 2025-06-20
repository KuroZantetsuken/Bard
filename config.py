import os
from dotenv import load_dotenv
class Config:
    """Stores all configuration constants for the bot."""
    load_dotenv()
    # Discord bot token from developer portal
    DISCORD_BOT_TOKEN = os.getenv("DISCORD_BOT_TOKEN")
    # Gemini API key
    GEMINI_API_KEY = os.getenv("GEMINI_API_KEY")
    # Text generation model
    MODEL_ID = "gemini-2.5-flash-preview-05-20"
    # Specific model for text-to-speech
    MODEL_ID_TTS = "gemini-2.5-flash-preview-tts"
    # Prebuilt voice name for text-to-speech
    VOICE_NAME = "Kore"
    # Emoji used to trigger a re-run of a prompt
    RETRY_EMOJI = '🔄'
    # Length used to trim responses (characters)
    MAX_MESSAGE_LENGTH = 2000
    # Max depth for fetching reply chains (messages)
    MAX_REPLY_DEPTH = 10
    # Budget for Gemini's thinking process (tokens)
    THINKING_BUDGET = 2048
    # Max tokens for Gemini's response
    MAX_OUTPUT_TOKENS = 65536
    # Sample rate used for text-to-speech (Hz)
    TTS_SAMPLE_RATE = 24000
    # Audio channels used in text-to-speech
    TTS_CHANNELS = 1
    # Sampling width for waveform generation (bytes)
    TTS_SAMPLE_WIDTH = 2
    # Fallback waveform for Discord voice messages if generation fails
    WAVEFORM_PLACEHOLDER = "FzYACgAAAAAAACQAAAAAAAA="
    # FFMPEG path
    FFMPEG_PATH = "ffmpeg"
    # Directory to load .prompt.md files from (folder)
    PROMPT_DIR = "prompts"
    # Directory to save and load .history.json files (folder)
    HISTORY_DIR = "history"
    # Directory to save and load .memory.json files (folder)
    MEMORY_DIR = "memories"
    # Directory where tool modules are located (folder)
    TOOLS_DIR = "tools"
    # Max number of user + assistant turn pairs (e.g., 16 turns = 32 content entries)
    MAX_HISTORY_TURNS = 0
    # Max age of turns considered for history (minutes) - 0 for disabled
    MAX_HISTORY_AGE = 0
    # Max number of memories to store and load per user
    MAX_MEMORIES = 32