# Bard Discord Bot: Comprehensive Technical Documentation

## Introduction

Welcome to the comprehensive technical documentation for the Bard Discord Bot. This document provides a deep dive into the bot's architecture, features, and operational mechanics. It is intended for developers, administrators, and anyone interested in understanding, maintaining, or extending the capabilities of this advanced AI assistant.

The Bard Discord Bot is an AI-powered agent designed for seamless integration into Discord servers. At its core, it leverages Google's powerful Gemini API to deliver a rich, interactive, and multimodal user experience. The bot is engineered for dynamic, context-aware conversations, featuring sophisticated capabilities such as function calling, long-term memory, and real-time adaptation to user interactions.

### Key Project Goals

*   **Intelligent Assistance:** To provide a highly responsive and intelligent AI assistant within the Discord ecosystem.
*   **Multimodal Interaction:** To showcase and utilize the advanced multimodal capabilities of the Gemini AI, including the processing of text, images, audio, video, and the generation of new images.
*   **Extensible Functionality:** To enable powerful, real-world actions through Gemini's function calling feature, integrating with external tools like Google Search and a code execution environment.
*   **Persistent Context:** To ensure continuous and contextually-aware interactions through robust short-term and long-term memory systems.

---

## 1. Getting Started

This section guides you through the process of setting up and running your own instance of the Bard Discord Bot.

### 1.1. Prerequisites

Before you begin, ensure your development environment meets the following requirements:

*   **Python:** Version 3.10 or newer is required.
*   **FFmpeg:** The FFmpeg library is essential for all audio and video processing tasks, including Text-to-Speech (TTS) and video attachment analysis. It must be installed and accessible from your system's PATH.
*   **Python Packages:** All required Python dependencies are listed in the [`requirements.txt`](requirements.txt) file.

### 1.2. Installation

Follow these steps to install the bot and its dependencies:

1.  **Clone the Repository:**
    ```bash
    git clone https://github.com/KuroZantetsuken/Bard.git
    cd Bard
    ```

2.  **Set up a Virtual Environment (Recommended):**
    ```bash
    python3 -m venv .venv
    source .venv/bin/activate
    ```

3.  **Install Dependencies:**
    ```bash
    pip install -r requirements.txt
    ```
    > **Note:** The `watchdog` library, used for the development hot-reloading feature, is included in [`requirements.txt`](requirements.txt).

### 1.3. Configuration

Proper configuration is crucial for the bot's operation.

1.  **Environment Variables:**
    *   Locate the `example.env` file and rename it to `.env`.
    *   Edit the `.env` file and provide the following essential values:
        *   `DISCORD_BOT_TOKEN`: Your unique bot token from the [Discord Developer Portal](https://discord.com/developers/applications).
        *   `GEMINI_API_KEY`: Your API key from [Google AI Studio](https://aistudio.google.com/) to access the Gemini API.
    *   **Note:** Environment variables are loaded explicitly during application startup after logging is configured, ensuring better visibility and error handling.

2.  **Discord Privileged Intents:**
    *   In the Discord Developer Portal, navigate to your bot's application settings and select the "Bot" tab.
    *   Enable the following privileged intents:
        *   **Presence Intent:** Allows the bot to receive user presence updates.
        *   **Server Members Intent:** Allows the bot to access the full list of members in a server, which is necessary for features like user context injection.

3.  **Prompt Customization (Optional):**
    *   The bot's core behavior, personality, and capabilities are defined by a series of prompt files located in the [`prompts/`](prompts/) directory.
    *   Any file in this directory ending with `.prompt.md` will be automatically loaded and concatenated to form the system prompt.
    *   You can customize the bot by adding, editing, or removing these files.

---

## 2. Bot Usage

Once the bot is running, you can interact with it in the following ways.

### 2.1. Interaction Methods

*   **Direct Messages (DMs):** The bot will respond to every message sent in a direct message channel.
*   **Server Channels:** In a server, the bot will respond when it is mentioned (`@<BotName>`).
*   **Replies:** The bot will respond if you reply to one of its messages with the mention toggle ON.

### 2.2. Retry a Response

To have the bot regenerate its last response, simply react to its message with the retry emoji: `🔄`. This will trigger the bot to re-process the original prompt and provide a new answer.

### 2.3. Cancel a Response

To cancel a response that is currently being generated, react to your own message with the cancel emoji: `🚫`. This will stop the bot from continuing its response.

---

## 3. Core Features

The Bard Discord Bot is equipped with a rich set of features that enable advanced and dynamic interactions.

### 3.1. Multimodal Understanding

The bot can process and comprehend a wide array of inputs beyond just text, thanks to the Gemini AI's native multimodal capabilities and a sophisticated internal `AttachmentProcessor`.

*   **Centralized URL Processing:** All URLs detected in a user's message, or in a message they are replying to, are routed through the `attachment_processor.check_and_process_url` method. This function intelligently identifies whether a URL points to a video or an image and processes it accordingly.
*   **Image Duplication Prevention:** The `PromptBuilder` includes a mechanism to prevent redundant image processing. It tracks already-processed images within a conversational turn, ensuring that images from direct attachments, replied messages, or URLs are only included once in the API prompt if their content or URL is identical.
*   **Enhanced Video Understanding:** The bot uses `yt-dlp` for advanced video analysis.
    *   For most video URLs (excluding YouTube, which Gemini handles directly), `yt-dlp` extracts comprehensive metadata (title, description, duration, etc.) without downloading the entire file. This metadata is then provided to the AI as textual context.
    *   Based on an estimated token cost, the bot decides whether to stream the full video content to the AI or just the audio track, ensuring efficient processing. This logic is encapsulated within the `_get_video_processing_details` helper method for clarity.
*   **Image Processing:** If a URL is identified as an image, it is uploaded to Gemini, allowing the AI to "see" and analyze its content.
*   **Web Page Analysis:** URLs that are not identified as video or image content are passed directly to the model. The AI can then intelligently decide to use its `InternetTool` to access and analyze the content of these web pages.

### 3.2. Context-Aware Conversations

The bot maintains two layers of memory to provide a coherent and personalized conversational experience.

*   **Short-Term Memory (History):**
    *   The bot keeps a record of recent conversations in-memory for the current channel or DM. This history is transient and not stored persistently on disk.
    *   It is accessible to all users in the channel and allows the AI to follow the conversational flow.
    *   The duration and number of turns retained are configurable in `config.py`.
*   **Long-Term Memory (User-Specific):**
    *   The bot can store user-specific information (e.g., preferences, key facts) for long-term recall.
    *   This memory is private to each user and persists across all servers where they interact with the bot.
    *   Memories are managed by the `MemoryTool` and stored locally in `data/`. The AI can be prompted to remove outdated or incorrect memories.

### 3.3. Dynamic Interaction & Adaptation

The bot is designed to be a dynamic participant in conversations.

*   **Response Adaptation:** If a user edits or deletes a message, the bot will automatically cancel any ongoing processing, delete its previous response, and re-evaluate the new or modified message.
*   **Response Cancellation:** Users can cancel a response that is currently being generated by reacting to their own message with the cancel emoji.
*   **Thread-Based Message Splitting:** For long text-only responses, the bot enhances readability by creating a thread. It sends the first sentence as a reply and then posts the remainder of the message in a new thread. To provide immediate context, the bot asynchronously generates a concise and relevant title for the thread using a dedicated, lightweight AI model. This keeps channels clean while providing the full response. All reaction emojis (retry, tool use) are placed on the first message that starts the thread.
*   **Discord Environment Context:** The bot injects a `[DYNAMIC_CONTEXT]` block into its prompts, providing the AI with information about its current environment, including the channel name, topic, users present, and the current time. This allows for more grounded and contextually relevant responses.
*   **Comprehensive Reply Chain Context:** When a user replies to a message, the bot traverses the entire reply chain up to a configurable depth (`MAX_REPLY_DEPTH`). It constructs a coherent conversational history, including both the text and all attachments from each message in the chain. This ensures the AI has a complete and accurate understanding of the multimodal context, regardless of whether the original messages are present in the bot's short-term history.

---

## 4. Extensible Functionality: Tools

The bot utilizes Gemini's function calling capability to connect with external tools, dramatically expanding its abilities beyond simple conversation. The AI autonomously decides when to use these tools to fulfill a user's request. When a tool is invoked, the bot adds a corresponding emoji reaction to its message as a visual indicator.

### 4.1. Memory Tool

*   **File:** [`bard/tools/memory.py`](bard/tools/memory.py)
*   **Emoji:** 🧠

This tool manages the bot's long-term, user-specific memory. It internally manages the `MemoryManager` for persistent storage.

*   **`add_user_memory`:**
    *   **Purpose:** To store important facts, preferences, or other details about a user for future recall.
    *   **Guidelines:** Should be used when a user explicitly asks the bot to remember something or provides information that is clearly intended for long-term retention.

*   **`remove_user_memory`:**
    *   **Purpose:** To remove outdated, incorrect, or no longer relevant information from a user's memory.
    *   **Guidelines:** Should be used when a user explicitly asks the bot to forget something or provides information that contradicts a stored memory.

### 4.2. Text-to-Speech (TTS) Tool

*   **File:** [`bard/tools/tts.py`](bard/tools/tts.py)
*   **Emoji:** 🗣️

This tool transforms the bot's textual responses into natural-sounding speech.

*   **`generate_speech_ogg`:**
    *   **Purpose:** To generate an audible response, enhancing accessibility and providing a more dynamic user experience.
    *   **Arguments:**
        *   `text_for_tts` (string, required): The text to convert to speech.
        *   `style` (string, optional): A parameter to influence the vocal style (e.g., tone, emotion).
    *   **Results:** The tool produces an OGG Opus audio file, its duration, and a base64-encoded waveform. This enables the bot to send audio as a native Discord voice message.
    *   **Guidelines:** Use when an audio response is explicitly requested or when a spoken reply would be more effective than text. Any text generated by the AI alongside the audio will be sent as a caption.
    *   **Optimization Note:** The `numpy` and `soundfile` libraries, used for waveform generation, are lazily loaded only when this function is called, reducing the bot's initial startup time.

### 4.3. Internet Tool

*   **File:** [`bard/tools/internet.py`](bard/tools/internet.py)
*   **Emoji:** 🌐

This tool allows the AI to access real-time information from the internet.

*   **`use_built_in_tools`:**
    *   **Purpose:** To answer questions about current events, verify facts, or analyze content from web pages that are outside its training data.
    *   **Arguments:**
        *   `search_query` (string, required): A concise query for a web search or the URL of a page to analyze.
    *   **Results:** The tool returns a summarized overview of the information found, including markdown-formatted links to the original sources for verification.
    *   **Guidelines:** Use for tasks requiring up-to-date information or analysis of external web content. Avoid using it for simple questions or tasks that can be answered from the AI's internal knowledge or solved with other tools like code execution.

### 4.4. Code Execution Tool

*   **File:** [`bard/tools/code.py`](bard/tools/code.py)
*   **Emoji:** 💻

This tool empowers the AI to write and execute Python code to solve complex problems.

*   **`execute_python_code`:**
    *   **Purpose:** To perform computations, manipulate data, run algorithms, or generate data visualizations.
    *   **Arguments:**
        *   `code_task` (string, required): A clear description of the task to be accomplished with Python code.
    *   **Results:** The tool returns the standard output (stdout) and standard error (stderr) from the executed script. If the code generates any image files (e.g., plots), they are returned as well. The executed Python code is also attached as a `code.py` file.
    *   **Guidelines:** Use for tasks that require calculation, data analysis, or logical problem-solving. Do not use for simple questions or tasks that can be answered from the AI's internal knowledge or solved with other tools like code execution.

### 4.5. Discord Event Tool

*   **File:** [`bard/tools/event.py`](bard/tools/event.py)
*   **Emoji:** 📅

This tool enables the AI to create and manage scheduled events directly within Discord servers.

*   **`create_discord_event`:**
    *   **Purpose:** Creates a new scheduled event in the Discord server.
    *   **Arguments:**
        *   `name` (string, required): The name of the event (maximum 100 characters).
        *   `description` (string, optional): A detailed description for the event (maximum 1000 characters). The AI can generate this if not explicitly provided.
        *   `start_time` (string, required): The scheduled start time in ISO 8601 format (e.g., "YYYY-MM-DDTHH:MM:SSZ").
        *   `end_time` (string, optional): The scheduled end time in ISO 8601 format. If omitted, the event will be created without an end time, suitable for ongoing events or those with flexible durations.
        *   `location` (string, optional): The location of the event (e.g., a website URL). If omitted, the AI will attempt to infer a suitable location, or default to the channel where the request was made ("Online" as a fallback).
        *   `image_url` (string, optional): A direct URL for the event's cover image (e.g., ending in .png, .jpg, .gif). The AI should use the InternetTool to find a suitable direct image URL if needed.
    *   **Results:** Upon successful creation, the tool returns details of the new event, including its ID, name, and a direct Discord event URL. No further AI action is required beyond acknowledging the creation.
    *   **Guidelines:** Only use this tool if event creation is explicitly requested. If the request pertains to a known topic (e.g., a game release, movie premiere), first use other tools (like the InternetTool) to find specific details such as the official date, time, description, and a relevant cover image URL.

*   **`delete_discord_event`:**
    *   **Purpose:** Deletes an existing scheduled event from the Discord server by its ID or name.
    *   **Arguments:**
        *   `id` (string, optional): The unique ID of the event to be deleted.
        *   `name` (string, optional): The name of the event to be deleted. Used if `id` is not provided.
    *   **Guidelines:** This action is permanent. The `get_discord_events` tool should be used first to obtain a list of events and their IDs for precise deletion. If only a name is provided and multiple events share a similar name, the AI should ask for clarification before proceeding.

*   **`get_discord_events`:**
    *   **Purpose:** Retrieves a list of scheduled events from the Discord server.
    *   **Arguments:** None.
    *   **Results:** The tool returns a list of dictionaries, each containing details about an active event, including its `id`, `name`, `description`, `start_time`, `end_time`, `location`, `status`, and `url`. If no events are found, an empty list is returned.
    *   **Guidelines:** Use this tool to get information about active events, which can then be used for other operations like deleting events or providing event listings to users.

### 4.6. Image Generation Tool

*   **File:** [`bard/tools/image.py`](bard/tools/image.py)
*   **Emoji:** 🎨

This tool allows the AI to generate new images based on textual descriptions using Gemini's image generation capabilities.

*   **`generate_image`:**
    *   **Purpose:** To create high-quality images from simple or complex text descriptions, supporting creative tasks requiring visual output.
    *   **Arguments:**
        *   `prompt` (string, required): A detailed text description for the image to be generated.
    *   **Results:** Upon successful generation, the tool returns information about the generated image, including its filename and confirmation of generation. The generated image will be sent as an attachment.
    *   **Guidelines:** Use this tool when a visual output is explicitly requested or when a detailed image generation task is provided. Provide comprehensive and descriptive prompts to guide the image generation process effectively.

### 4.7. Diagnose Tool

*   **File:** [`bard/tools/diagnose.py`](bard/tools/diagnose.py)
*   **Emoji:** 🔍

This tool allows the AI to inspect its own project structure and file contents.

*   **`inspect_project`:**
    *   **Purpose:** To inspect its own project structure and file contents, which is essential for self-diagnosis, understanding the existing codebase, and verifying changes. It provides a way for the AI to dynamically explore its own environment.
    *   **Arguments:**
        *   `path` (string, required): The relative path to the file or folder to be inspected. Use '.' to inspect the project's root directory.
    *   **Results:** If a path to a folder is provided, the tool returns a JSON object representing the folder's file hierarchy, including nested files and directories. If a path to a file is provided, it returns the raw content of that file as a string.
    *   **Guidelines:** Use this tool when you need to understand the structure of the project or the content of a specific file. It is a read-only tool and does not modify any files or directories. Do not use this tool to execute code or interact with external services.

---

## 5. Development Workflow

### 5.1. Hot Reloading

To streamline the development process, the project includes a hot-reloading feature powered by the `watchdog` library. This system automatically restarts the bot whenever changes are detected in critical files, eliminating the need for manual restarts.

**How It Works:**

The [`hotloading.py`](hotloading.py) script monitors the entire project directory for modifications to `.py`, `.env`, and `.prompt.md` files. This ensures that changes to the source code, environment configuration, or AI prompts will trigger an automatic restart. To prevent excessive restarts, it uses a debouncing mechanism: after a file change is detected, it waits for a short period of inactivity (2 seconds) before triggering a single, graceful restart of the bot process.

**Usage:**

1.  Ensure all dependencies, including `watchdog`, are installed:
    ```bash
    pip install -r requirements.txt
    ```
2.  To start the bot in development mode, run the `hotloading.py` script instead of `main.py`:
    ```bash
    python3 hotloading.py
    ```
3.  The bot will now restart automatically when you save changes to a relevant file. Press `Ctrl+C` to stop the hot-reloader.

---

## 6. Technical Architecture

The bot's architecture is designed to be modular and maintainable, with a clear separation of concerns across different packages and modules.

### 6.1. Project Structure

```
.
├── .gitignore
├── config.py                   # Centralized configuration constants
├── DOCUMENTATION.md            # This documentation file
├── example.env                 # Example environment variables file
├── hotloading.py               # Script for development hot-reloading
├── LICENSE                     # Project license
├── main.py                     # Main entry point for the application
├── README.md                   # Project README
├── requirements.txt            # Python dependencies
├── bard/                       # Main application source code
│   ├── ai/                     # AI-related functionalities (Gemini API interaction)
│   │   ├── config.py           # AI configuration and settings management
│   │   ├── chat/               # Chat-specific AI components
│   │   │   ├── conversation.py # Main conversation flow and tool calling logic
│   │   │   ├── responses.py    # Extraction of data from Gemini API responses
│   │   │   └── titler.py       # Generates titles for long-response threads
│   │   ├── context/            # Context management for AI prompts
│   │   │   ├── dynamic.py      # Manages dynamic Discord context injection
│   │   │   ├── history.py      # In-memory chat history management
│   │   │   ├── prompts.py      # Central manager for constructing prompts for the Gemini API
│   │   │   ├── replies.py      # Constructs reply chain context from messages
│   │   │   └── videos.py       # Formats video metadata for context
│   │   ├── core.py             # Core Gemini API client and interaction logic
│   │   ├── files.py            # Media attachment processing and uploading
│   │   └── types.py            # Shared type definitions for AI components
│   ├── bot/                    # Discord-specific functionalities
│   │   ├── core/               # Core bot components
│   │   │   ├── container.py    # Dependency injection container
│   │   │   ├── coordinator.py  # Orchestrates message processing workflow
│   │   │   └── handlers.py     # Discord event listeners (on_message, on_ready, etc.)
│   │   ├── lifecycle/          # Discord event handling and bot presence
│   │   │   ├── events.py       # Handles Discord events that modify in-flight processes
│   │   │   └── presence.py     # Manages the bot's Discord presence
│   │   ├── message/            # Message processing and sending components
│   │   │   ├── manager.py      # Manages bot messages and their state (e.g., in-flight responses)
│   │   │   ├── parser.py       # Parses Discord messages into structured data
│   │   │   ├── reactions.py    # Manages message reactions
│   │   │   ├── sender.py       # Logic for sending messages and files to Discord
│   │   │   ├── threading.py    # Manages long-response threads
│   │   │   └── voice.py        # Manages sending native voice messages
│   │   ├── bot.py              # Main bot initialization and event handling setup
│   │   └── types.py            # Shared type definitions for the bot
│   ├── tools/                  # Gemini function calling tools
│   │   ├── base.py             # Base classes and protocols for tools
│   │   ├── code.py             # Python code execution tool
│   │   ├── event.py            # Discord event management tool
│   │   ├── image.py            # Image generation tool
│   │   ├── internet.py         # Google Search and URL analysis tool
│   │   ├── memory.py           # User memory management tool, including MemoryManager
│   │   ├── registry.py         # Tool discovery and registration
│   │   └── tts.py              # Text-to-speech tool
│   └── util/                   # General-purpose helper functions
│       ├── data/               # Data-related utilities
│       │   ├── parser.py       # Parses various data formats (e.g., HTML)
│       │   └── storage.py      # Base class for JSON file storage
│       ├── media/              # Media processing utilities
│       │   ├── audio.py        # Audio processing utilities
│       │   ├── ffmpeg.py       # Wrapper for FFmpeg commands
│       │   ├── media.py        # Media URL extraction and MIME type detection
│       │   └── video.py        # Video processing utilities
│       ├── system/             # System-level utilities
│       │   ├── files.py        # Shared utility for creating temporary files
│       │   └── lifecycle.py    # Manages asynchronous task lifecycles
│       └── logging.py          # Custom logging configuration
├── data/                       # Runtime data storage
└── prompts/                    # System prompt templates for the AI
    ├── capabilities.prompt.md
    └── personality.prompt.md
```

### 6.2. Key Components

#### `main.py`: Application Entry Point

The [`main.py`](main.py) script is the starting point of the application. Its primary responsibilities are to set up the application-wide logging system and launch the bot's asynchronous event loop. Environment variable validation is handled within the `config.Config` class.

#### `bard/ai/` Package

This package contains all logic related to interacting with the Google Gemini API, including managing conversational flow and prompt construction.

*   [`bard/ai/core.py`](bard/ai/core.py): Provides the `GeminiCore` class, a wrapper around the Gemini API client that handles content generation and media uploads. The `generate_content` method supports streaming directly via a `stream=True` argument.
*   [`bard/ai/config.py`](bard/ai/config.py): The `GeminiConfigManager` class is responsible for creating the generation configuration for Gemini API calls.
*   [`bard/ai/chat/conversation.py`](bard/ai/chat/conversation.py): The `AIConversation` class manages the entire, stateful, multi-step conversational turn with the Gemini API. It orchestrates prompt building, history management, AI model interaction, and tool calling, consolidating the final AI response. It directly instantiates and uses `MemoryManager` for loading and formatting user memories. Logic for processing tool responses and building the final AI response resides in dedicated helper methods (`_process_tool_response_part` and `_build_final_response_data`).
*   [`bard/ai/context/history.py`](bard/ai/context/history.py): The `ChatHistoryManager` is responsible for managing in-memory short-term conversational history.
*   [`bard/ai/context/replies.py`](bard/ai/context/replies.py): The `ReplyChainConstructor` is a specialized class that traverses a chain of Discord message replies. It constructs a coherent, formatted string of the conversation and gathers all attachments from each message in the chain, up to a configurable depth. This ensures the AI receives a complete multimodal context for its responses.
*   [`bard/ai/files.py`](bard/ai/files.py): Contains the `AttachmentProcessor`, a critical component for handling all media. It processes local attachments and remote URLs, uploads them to the Gemini File API, and caches the results. The `upload_media_bytes` method handles media processing from bytes.
*   [`bard/ai/context/prompts.py`](bard/ai/context/prompts.py): The `PromptBuilder` class acts as a central manager for constructing the final prompt sent to the AI. It combines system instructions, chat history, user memories, processed attachments, and dynamic context. It leverages `DynamicContextFormatter` for injecting Discord environment context and `VideoFormatter` for managing video metadata context, ensuring a comprehensive and accurate prompt.
*   [`bard/ai/chat/responses.py`](bard/ai/chat/responses.py): The `ResponseExtractor` utility helps parse and extract textual content and other data from the AI's response.
*   [`bard/ai/chat/titler.py`](bard/ai/chat/titler.py): The `ThreadTitler` is a specialized service that generates concise, context-aware titles for Discord threads created from long bot responses. It uses a separate, lightweight AI model for fast and efficient title generation.

#### `bard/bot/` Package

This package encapsulates all Discord-specific functionality and orchestrates the bot's responses to user interactions through a series of specialized, single-responsibility components.

*   [`bard/bot/bot.py`](bard/bot/bot.py): Initializes all core components and sets up the `BotHandlers` cog, which contains the listeners for all Discord events. The `on_ready` event logic for setting the bot's user ID in other relevant components.
*   [`bard/bot/core/container.py`](bard/bot/core/container.py): The `Container` class manages dependency injection, instantiating and providing access to all major services like the `Coordinator`, `AIConversation`, and `ThreadTitler`.
*   [`bard/bot/core/handlers.py`](bard/bot/core/handlers.py): Defines the `BotHandlers` class, a `commands.Cog` that acts as the raw entry point for `discord.py` events, delegating them immediately to the appropriate handlers without additional logic. The `on_ready` method contains logic to set the bot's user ID and delegates presence updates to the `PresenceManager`.
*   [`bard/bot/core/coordinator.py`](bard/bot/core/coordinator.py): The `Coordinator` orchestrates the high-level workflow for a single message processing run. It delegates to the `MessageParser` for input parsing, the `AIConversation` for AI interaction, the `MessageSender` for sending responses, and the `ReactionManager` for handling message reactions, ensuring a cohesive flow from message reception to final reply.
*   [`bard/bot/lifecycle/events.py`](bard/bot/lifecycle/events.py): The `DiscordEventHandler` contains the specific business logic for handling Discord events that modify an ongoing process, such as message edits, deletions, and retry reactions. It coordinates with the `TaskLifecycleManager` to reprocess or cancel tasks as needed. When a user's message is edited or deleted, it correctly handles the cleanup of the bot's response, ensuring that if the response started a thread, only the initial message is deleted, preserving the thread's history.
*   [`bard/bot/message/parser.py`](bard/bot/message/parser.py): The `MessageParser` transforms a raw `discord.Message` object into a clean, structured `ParsedMessageContext` dataclass. It delegates reply chain processing to the `ReplyChainConstructor` and handles the extraction of message content, attachments, and other Discord context, preparing the data for AI interaction.
*   [`bard/bot/message/sender.py`](bard/bot/message/sender.py): The `MessageSender` handles all outbound communication to Discord. It delegates the complex tasks of sending voice messages, creating threads for long responses, and managing temporary files to specialized managers, focusing solely on the final act of sending the message content.
*   [`bard/bot/lifecycle/presence.py`](bard/bot/lifecycle/presence.py): The `PresenceManager` is responsible for setting the bot's Discord presence (activity status).
*   [`bard/bot/message/reactions.py`](bard/bot/message/reactions.py): The `ReactionManager` is responsible for adding and removing reactions on bot messages.
*   [`bard/bot/message/threading.py`](bard/bot/message/threading.py): The `ThreadingManager` is responsible for creating and managing threads for long bot responses.
*   [`bard/bot/message/voice.py`](bard/bot/message/voice.py): The `VoiceManager` handles the logic for sending native Discord voice messages.
*   [`bard/bot/types.py`](bard/bot/types.py): Defines shared data structures and type hints used across the bot components.
*   [`bard/bot/message/manager.py`](bard/bot/message/manager.py): Manages bot messages and their state (e.g., tracking in-flight responses for cancellation or retry).

#### `bard/tools/` Package

This package contains the implementations of the external functions the AI can call.

*   [`bard/tools/base.py`](bard/tools/base.py): Defines the `BaseTool` abstract class and the `ToolContext` container, providing a consistent structure for all tools. The `GeminiClientProtocol` defines `generate_content` as its primary method. The `AttachmentProcessorProtocol` specifies `upload_media_bytes`.
*   [`bard/tools/code.py`](bard/tools/code.py): Python code execution tool. It utilizes `self.context.mime_detector.get_extension` for retrieving file extensions.
*   [`bard/tools/image.py`](bard/tools/image.py): Image generation tool.
*   [`bard/tools/internet.py`](bard/tools/internet.py): Google Search and URL analysis tool. It employs standard Python list types and streamlines checks for `gemini_client` and `response_extractor` in the `execute_tool` method.
*   [`bard/tools/memory.py`](bard/tools/memory.py): User memory management tool. This file contains the `MemoryManager` class. It employs standard Python list types.
*   [`bard/tools/registry.py`](bard/tools/registry.py): Tool discovery and registration.
*   [`bard/tools/tts.py`](bard/tools/tts.py): Text-to-speech tool. It employs `self.gemini_client.generate_content` with `stream=True` for speech synthesis.
*   [`bard/tools/diagnose.py`](bard/tools/diagnose.py): Project inspection tool.

#### `bard/util/` Package

This package provides shared, general-purpose helper modules.

*   [`bard/util/media/ffmpeg.py`](bard/util/media/ffmpeg.py): A wrapper for executing FFmpeg commands asynchronously for audio conversion and processing. The `convert_audio` method is a class method and uses `cls.execute` for internal FFmpeg command execution.
*   [`bard/util/system/files.py`](bard/util/system/files.py): Contains a shared utility for creating and managing temporary files, used across different modules for handling attachments and other file-based operations.
*   [`bard/util/system/lifecycle.py`](bard/util/system/lifecycle.py): The `TaskLifecycleManager` manages the complete `asyncio.Task` lifecycle for message processing runs. It handles the creation, cancellation, and monitoring of asynchronous tasks, ensuring proper cleanup and error logging.
*   [`bard/util/logging.py`](bard/util/logging.py): Configures the application's advanced logging system, which supports separate handlers for console and file output, log pruning, and sanitization of sensitive data in logs.
*   [`bard/util/media/media.py`](bard/util/media/media.py): Contains helper functions for extracting URLs from text and detecting MIME types.
*   [`bard/util/data/parser.py`](bard/util/data/parser.py): Parses various data formats, for example, extracting image URLs from HTML content.
*   [`bard/util/data/storage.py`](bard/util/data/storage.py): Provides a base class for managing data stored in JSON files.
*   [`bard/util/media/video.py`](bard/util/media/video.py): Contains helper functions for processing videos. The `stream_media` method utilizes `FFmpegWrapper.execute` for consistent and robust FFmpeg command execution.
*   [`bard/util/media/audio.py`](bard/util/media/audio.py): Provides utilities for audio processing.

### 6.3. Dependency Management

To address circular import dependencies, particularly between modules like `bard/util/system/lifecycle.py` and `bard/bot/core/coordinator.py`, a strategy of deferred dependency resolution combined with type-checking imports is employed.

*   **Problem:** Direct module-level imports between two modules, where each module needs to reference the other's classes for type hinting or instantiation, can lead to `ImportError` due to circular dependencies at runtime.
*   **Solution:**
    1.  **Deferred Instantiation:** Components are designed such that they can be instantiated without immediate access to all their dependencies. For example, `TaskLifecycleManager` can be created without a `Coordinator` instance, and the `Coordinator` can be injected later.
    2.  **Runtime Injection:** The `bard/bot/bot.py` module acts as an orchestrator, instantiating `TaskLifecycleManager` and `Coordinator` independently, and then explicitly injecting the `Coordinator` instance into the `TaskLifecycleManager` after both are available. This is managed by the `Container` class.
    3.  **Type Hinting with `TYPE_CHECKING`:** For static analysis (type checking with tools like Pylance) to function correctly without introducing runtime circular imports, the `typing.TYPE_CHECKING` constant is used. This allows modules to conditionally import types only during type-checking passes, effectively making those imports inert at runtime.

This approach ensures that the codebase maintains strong type hints for development and static analysis while avoiding runtime import errors caused by interdependent modules.

---

## 7. Configuration Reference

The [`config.py`](config.py) file centralizes all static configuration variables for the bot, loading sensitive keys from environment variables and defining parameters that control the bot's behavior.

*   **`MODEL_ID`**: The primary Gemini model for general conversation and reasoning.
*   **`MODEL_ID_TTS`**: The specialized Gemini model for text-to-speech generation.
*   **`MODEL_ID_TITLER`**: The lightweight Gemini model used for generating thread titles.
*   **`MODEL_ID_IMAGE_GENERATION`**: The specialized Gemini model for image generation.

### Bot Presence Settings

You can customize the bot's presence on Discord by editing the following variables in `config.py`:

*   **`PRESENCE_TYPE`**: A string that determines the type of activity. Valid options are:
    *   `"playing"`: Sets the status to "Playing \[PRESENCE\_TEXT]".
    *   `"listening"`: Sets the status to "Listening to \[PRESENCE\_TEXT]".
    *   `"watching"`: Sets the status to "Watching \[PRESENCE\_TEXT]".
    *   `"custom"`: Sets a custom status with an optional emoji.
*   **`PRESENCE_TEXT`**: The text that appears in the status.
*   **`PRESENCE_EMOJI`**: The emoji to display next to the status text. This is only used when `PRESENCE_TYPE` is set to `"custom"`.

**Known Limitation:**
Unicode emojis set via `PRESENCE_EMOJI` for a custom status might not display correctly in the Discord client, despite being correctly handled by `discord.py`. This appears to be a limitation of the Discord API or client-side rendering, and not an issue with the bot's implementation.

---

## 8. License

This project is released into the public domain under the **Unlicense**.

This is free and unencumbered software. You are free to copy, modify, publish, use, compile, sell, or distribute this software, in source code or binary form, for any purpose, commercial or non-commercial, and by any means.

For the full license text, please refer to the [Unlicense website](https://unlicense.org).
