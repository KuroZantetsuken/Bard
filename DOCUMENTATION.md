# Bard Discord Bot: Comprehensive Technical Documentation

## Introduction

Welcome to the comprehensive technical documentation for the Bard Discord Bot. This document provides a deep dive into the bot's architecture, features, and operational mechanics. It is intended for developers, administrators, and anyone interested in understanding, maintaining, or extending the capabilities of this advanced AI assistant.

The Bard Discord Bot is an AI-powered agent designed for seamless integration into Discord servers. At its core, it leverages Google's powerful Gemini API to deliver a rich, interactive, and multimodal user experience. The bot is engineered for dynamic, context-aware conversations, featuring sophisticated capabilities such as function calling, long-term memory, and real-time adaptation to user interactions.

### Key Project Goals

*   **Intelligent Assistance:** To provide a highly responsive and intelligent AI assistant within the Discord ecosystem.
*   **Multimodal Interaction:** To showcase and utilize the advanced multimodal capabilities of the Gemini AI, including the processing of text, images, audio, video, and the generation of new images.
*   **Extensible Functionality:** To enable powerful, real-world actions through Gemini's function calling feature, integrating with external tools like Google Search and a code execution environment.
*   **Persistent Context:** To ensure continuous and contextually-aware interactions through robust long-term memory systems.

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
    *   The bot's core behavior, personality, and capabilities are defined by a series of prompt files located in the [`data/prompts/`](data/prompts/) directory.
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

The bot can process and comprehend a wide array of inputs beyond just text, thanks to the Gemini AI's native multimodal capabilities and a sophisticated internal processing pipeline.

*   **Centralized URL Processing:** All URLs detected in a user's message are routed through the [`ScrapingOrchestrator`](bard/scraping/orchestrator.py), which manages a robust, multi-stage process.
    1.  **Cache Check:** The orchestrator first checks the [`CacheManager`](bard/scraping/cache.py) for a recent, valid copy of the URL's content using the `get_from_cache` method. If a hit is found, the cached data is returned immediately.
    2.  **Live Processing:** If the URL is not in the cache, the orchestrator concurrently runs the [`Scraper`](bard/scraping/scraper.py) and the [`VideoHandler`](bard/scraping/video.py).
        *   **Web Page Scraping:** The `Scraper` uses `Playwright` to launch a headless browser with the `uBlock Origin` and `Consent-O-Matic` extensions to block ads and automatically handle cookie banners. The scraper is configured to be aggressive in its blocking. It has a 30-second timeout and will retry up to three times with exponential backoff to handle transient network issues. It extracts the text content and a full-page screenshot.
        *   **Video Detection:** The `VideoHandler` uses `yt-dlp` to determine if the URL points to a video and extracts its metadata.
    3.  **Data Structuring:** The results from the scraper and video handler are combined into a single `ScrapedData` object.
    4.  **Caching:** The `ScrapingOrchestrator` instructs the `CacheManager` to save the new `ScrapedData` object for future requests using the `set_to_cache` method.

*   **Caching Mechanism:**
    *   The [`CacheManager`](bard/scraping/cache.py) stores cached data in a structured directory format. Each domain (e.g., `theverge.com`) has its own subdirectory within the `CACHE_DIR`.
    *   For each scraped URL, a JSON file is created containing the text content, metadata, and other relevant information.
    *   Screenshots are saved as PNG files with the same name as the JSON file, but with a `.png` extension. This keeps the screenshot and its corresponding data together.

*   **Direct Attachment Handling:** The [`MessageParser`](bard/bot/message/parser.py) gathers all direct attachments from the current message and the reply chain. The [`AIConversation`](bard/ai/chat/conversation.py) service then uses the [`AttachmentProcessor`](bard/ai/files.py) to upload the media to the Gemini File API, making it available to the model.

*   **Overall Goal:** This comprehensive pipeline ensures the AI always has sufficient information from URLs and direct attachments to simulate human-level understanding, enabling it to process and respond to web content and user-provided files with a level of comprehension akin to a human observer.

### 3.2. Context-Aware Conversations

The bot maintains a layer of memory to provide a coherent and personalized conversational experience.

*   **Long-Term Memory (User-Specific):**
    *   The bot can store user-specific information (e.g., preferences, key facts) for long-term recall.
    *   This memory is private to each user and persists across all servers where they interact with the bot.
    *   Memories are managed by the `MemoryTool` and stored locally in `data/`. The AI can be prompted to remove outdated or incorrect memories.

### 3.3. Dynamic Interaction & Adaptation

The bot is designed to be a dynamic participant in conversations, capable of adapting to real-time user actions. This is primarily managed by the [`DiscordEventHandler`](bard/bot/lifecycle/events.py).

*   **Response Adaptation (Edits & Deletions):** If a user edits a message that the bot is in the process of responding to, the `DiscordEventHandler` cancels the current task and starts a new one with the updated content. If a message is deleted, the corresponding task is cancelled, and any bot responses are removed. This ensures the bot's output always reflects the latest user input.
*   **Response Cancellation:** Users can cancel a response that is currently being generated by reacting to their own message with the cancel emoji (`🚫`). The `DiscordEventHandler` detects this and stops the processing task.
*   **Response Retry:** Users can request a new response by reacting to a bot's message with the retry emoji (`🔄`). The `DiscordEventHandler` verifies the reactor is the original author and re-runs the generation process for the initial prompt.
*   **Thread-Based Message Splitting:** For long text-only responses, the bot enhances readability by creating a thread. It sends the first sentence as a reply and then posts the remainder of the message in a new thread. To provide immediate context, the bot asynchronously generates a concise and relevant title for the thread using a dedicated, lightweight AI model. This keeps channels clean while providing the full response. All reaction emojis (retry, tool use) are placed on the first message that starts the thread.
*   **Discord Environment Context:** The bot injects a dynamic context block into its prompts to provide the AI with real-time information about its current environment. This block, wrapped in `[CONTEXT:START]` and `[CONTEXT:END]` tags, includes the channel name, topic, a list of present users, and the current UTC time, allowing for more grounded and contextually relevant responses.
*   **Comprehensive Reply Chain Context:** When a user replies to a message, the bot traces the conversation backward through the reply chain, up to a configurable depth (`MAX_REPLY_DEPTH`). The [`ReplyChainConstructor`](bard/ai/context/replies.py) class handles this by fetching each parent message, collecting its textual content and any attachments. It then assembles this information into a single, formatted string that clearly delineates the conversation for the AI. Crucially, it also gathers all attachments from every message in the chain, providing a complete multimodal context. This ensures the AI can understand the full scope of the conversation, including all shared images and files.

---

## 4. Extensible Functionality: Tools

The bot utilizes Gemini's function calling capability to connect with external tools, dramatically expanding its abilities beyond simple conversation. The AI autonomously decides when to use these-tools to fulfill a user's request. When a tool is invoked, the bot adds a corresponding emoji reaction to its message as a visual indicator.

### 4.1. Memory Tool

*   **File:** [`bard/tools/memory.py`](bard/tools/memory.py)
*   **Emoji:** 🧠

This tool provides the AI with a reliable mechanism for long-term, user-specific memory. It serves as a high-level interface to the `MemoryManager`, which handles the persistent storage of user-specific information. The `MemoryTool` exposes functions for adding and removing memories, which the AI can call to maintain a personalized context across conversations.

*   **`add_user_memory`:**
    *   **Purpose:** To store important facts, preferences, or other details about a user for future recall. This function allows the AI to build a persistent understanding of the user.
    *   **Guidelines:** Should be used when a user explicitly asks the bot to remember something (e.g., "Remember my birthday is in October") or provides information that is clearly intended for long-term retention. Avoid using it for transient conversational details.

*   **`remove_user_memory`:**
    *   **Purpose:** To remove outdated, incorrect, or no longer relevant information from a user's memory, ensuring the AI's knowledge base remains accurate.
    *   **Guidelines:** Should be used when a user explicitly asks the bot to forget something or provides new information that directly contradicts a stored memory. The AI should use the `id` from the memory context to specify which item to delete.

### 4.2. Text-to-Speech (TTS) Tool

*   **File:** [`bard/tools/tts.py`](bard/tools/tts.py)
*   **Emoji:** 🗣️

This tool transforms the bot's textual responses into natural-sounding speech, leveraging the Gemini TTS API.

*   **`generate_speech_ogg`:**
    *   **Purpose:** To generate an audible response, enhancing accessibility and providing a more dynamic user experience.
    *   **Arguments:**
        *   `text_for_tts` (string, required): The text to convert to speech.
        *   `style` (string, optional): A parameter to influence the vocal style (e.g., tone, emotion).
    *   **Results:** The tool orchestrates a multi-step process to generate a native Discord voice message. It first obtains raw PCM audio data from the Gemini TTS API and pipes it directly to FFmpeg for efficient conversion into the OGG Opus format. Subsequently, it analyzes the generated audio to calculate its exact duration and produce a base64-encoded visual waveform. The final output—a tuple containing the OGG Opus audio bytes, its duration, and the waveform—is precisely what Discord requires to display a native, interactive voice message.
    *   **Guidelines:** Use when an audio response is explicitly requested or when a spoken reply would be more effective than text. Any text generated by the AI alongside the audio will be sent as a caption.

### 4.3. Search Tool

*   **File:** [`bard/tools/search.py`](bard/tools/search.py)
*   **Emoji:** 🌐

This tool empowers the AI to access and process real-time information from the internet by leveraging the Gemini API's built-in Google Search functionality. It provides a secure and powerful way to answer questions about current events or verify facts.

*   **`search_internet`:**
    *   **Purpose:** To perform a web search to answer questions that are outside the AI's internal knowledge base.
    *   **Arguments:**
        *   `search_query` (string, required): A concise query for a web search.
    *   **Process:**
        1.  When invoked, the tool makes a secondary, internal call to the Gemini API.
        2.  This internal call is specially configured to enable the `GoogleSearch` tool, instructing the model to use its native search capabilities to fulfill the `search_query`.
    *   **Results:** The tool captures the summarized output from the search. The Gemini API automatically provides `grounding_metadata`, which this tool processes to extract and format source links. The final result includes a summarized overview of the information found and markdown-formatted links to the original sources for user verification.
        *   **Grounding Source Scraping:** After the search, the [`AIConversation`](bard/ai/chat/conversation.py) service extracts the grounding source URLs from the response metadata and passes them to the `ScrapingOrchestrator`'s `process_grounding_urls` method. This triggers the standard scraping workflow for each source, providing the AI with additional context, especially visual (screenshots), for the pages it used to generate the search-based answer.
    *   **Guidelines:** Use for tasks requiring up-to-date information. Avoid using it for simple questions or tasks that can be answered from the AI's internal knowledge or solved with other tools like code execution.

### 4.4. Code Execution Tool

*   **File:** [`bard/tools/code.py`](bard/tools/code.py)
*   **Emoji:** 💻

This tool empowers the AI to programmatically solve complex problems by leveraging the Gemini API's built-in, sandboxed code execution environment. This provides a secure and powerful way to perform computations, manipulate data, and generate visualizations without executing code directly on the bot's host machine.

*   **`execute_python_code`:**
    *   **Purpose:** To perform computations, run algorithms, or create data visualizations in a secure, sandboxed environment.
    *   **Arguments:**
        *   `code_task` (string, required): A clear description of the task to be accomplished with Python code.
    *   **Process:**
        1.  When invoked, the tool makes a secondary, internal call to the Gemini API.
        2.  This internal call is specially configured to enable the `code_execution` tool, instructing the model to generate and run Python code to fulfill the `code_task`.
        3.  The model executes the code within Google's secure, sandboxed environment.
    *   **Results:** The tool captures the complete output from the execution, including:
        *   **Standard Output & Error:** Any text printed to `stdout` or `stderr` is returned.
        *   **Generated Images:** If the code produces a plot or any other image, the tool captures the image data.
        *   **Executed Code:** For transparency, the exact Python code that was executed is captured and attached as a `code.py` file to the bot's response.
    *   **Guidelines:** Use for tasks that require calculation, data analysis, or logical problem-solving. Do not use for simple questions or for tasks that can be answered from the AI's internal knowledge or solved with other tools.

### 4.5. Discord Event Tool

*   **File:** [`bard/tools/event.py`](bard/tools/event.py)
*   **Emoji:** 📅

This tool enables the AI to create and manage scheduled events directly within Discord servers.

*   **`create_discord_event`:**
    *   **Purpose:** Creates a new scheduled event in the Discord server.
    *   **Arguments:**
        *   `name` (string, required): The name of the event (maximum 100 characters).
        *   `start_time` (string, required): The scheduled start time in ISO 8601 format (e.g., `"2025-09-01T17:00:00Z"`).
        *   `description` (string, optional): A detailed description for the event (maximum 1000 characters). The AI can generate this if not provided.
        *   `end_time` (string, required): The scheduled end time in ISO 8601 format. Required if `location` is specified.
        *   `location` (string, optional): The location of the event (e.g., a website URL). If provided, `end_time` must also be set. If omitted, the event defaults to the channel where the request was made.
        *   `image_url` (string, optional): A direct URL for the event's cover image (e.g., ending in .png, .jpg).
    *   **Results:** Upon success, returns the ID, name, and URL of the newly created event.
    *   **Guidelines:** Only use if event creation is requested. If the request involves a known topic (e.g., a game release), use the `SearchTool` first to find official details like date, time, and a relevant cover image URL.

*   **`delete_discord_event`:**
    *   **Purpose:** Deletes an existing scheduled event from the Discord server. This action is permanent.
    *   **Arguments:**
        *   `id` (string, optional): The unique ID of the event to be deleted.
        *   `name` (string, optional): The name of the event to be deleted.
    *   **Results:** Confirms the successful deletion of the event.
    *   **Guidelines:** The `get_discord_events` tool should be used first to obtain a list of events and their IDs for precise deletion. Using the `id` is preferred over the `name` to avoid ambiguity. If only a name is provided and multiple events share it, the tool will return an error, and the AI should ask the user for clarification.

*   **`get_discord_events`:**
    *   **Purpose:** Retrieves a list of all scheduled events from the Discord server.
    *   **Arguments:** None.
    *   **Results:** Returns a list of event objects, where each object contains the event's `id`, `name`, `description`, `start_time`, `end_time`, `location`, `status`, and `url`. If no events are found, it returns an empty list.
    *   **Guidelines:** Use this tool to get information about active events, which can then be used for other operations like deleting or updating events.

### 4.6. Image Generation Tool

*   **File:** [`bard/tools/image.py`](bard/tools/image.py)
*   **Emoji:** 🎨

This tool allows the AI to generate high-quality images from a text description using the Gemini API.

*   **`generate_image`:**
    *   **Purpose:** To create a new image based on a detailed text prompt. This is ideal for creative tasks requiring visual output, such as illustrations, photorealistic scenes, or other visual assets.
    *   **Arguments:**
        *   `prompt` (string, required): A detailed and descriptive text prompt to guide the image generation process.
    *   **Results:** The tool generates an image, which is sent as an attachment in the Discord channel. It returns a confirmation message, including the filename of the generated image.
    *   **Guidelines:** Use when a user requests an image. The AI should provide a comprehensive and descriptive prompt to ensure the best results.

### 4.7. Diagnose Tool

*   **File:** [`bard/tools/diagnose.py`](bard/tools/diagnose.py)
*   **Emoji:** 🔍

This tool provides the AI with the ability to inspect its own project files, enabling it to understand the codebase, verify file structures, and diagnose issues by examining source code or logs. It is a read-only tool that cannot modify the file system.

*   **`inspect_project`:**
    *   **Purpose:** To inspect the project's file system. This is essential for the AI's self-diagnosis, allowing it to understand the existing codebase and dynamically explore its own environment.
    *   **Arguments:**
        *   `path` (string, required): The relative path to the file or folder to inspect. Use `.` to inspect the project's root directory.
    *   **Results:**
        *   If the path points to a **folder**, the tool returns a JSON object representing the folder's file hierarchy, including all nested files and subdirectories.
        *   If the path points to a **file**, the tool returns the raw content of that file as a string.
    *   **Guidelines:** Use this tool to understand the project structure or read the content of a specific file. The tool respects the project's `.gitignore` file and will refuse to read matching files, with the exception of the `logs` directory, which is always accessible for diagnostic purposes.

---

## 5. System Architecture

This section delves into the high-level architecture of the bot, explaining how the different packages and modules collaborate to bring the bot to life.

### 5.1. The `bard/bot` Package

The `bard/bot` package is the heart of the bot's interaction logic, responsible for handling Discord events, processing messages, and managing the bot's lifecycle.

#### 5.1.1. `bard/ai/chat/conversation.py`: The AI's Brain

*   **[`bard/ai/chat/conversation.py`](bard/ai/chat/conversation.py):** The `AIConversation` class is the central nervous system of the bot's AI logic. It orchestrates the entire process of generating a response, from building the initial prompt to handling complex, multi-step tool calls. Its responsibilities include:
    *   **Prompt Construction:** It uses the [`PromptBuilder`](bard/ai/context/prompts.py) to assemble all contextual information—including user messages, attachments, scraped data, and system instructions—into a coherent prompt for the Gemini model.
    *   **Main AI Call:** It makes the primary call to the Gemini API to get the initial response.
    *   **Tool Execution Loop:** If the model requests to use a tool, `AIConversation` enters a loop. It calls the appropriate tool via the [`ToolRegistry`](bard/tools/registry.py), sends the tool's output back to the model, and waits for the next response, continuing this process until the model generates a final answer.
    *   **Response Finalization:** It consolidates all the text, media, and tool usage indicators from the conversation into a single `FinalAIResponse` object, which is then passed to the `MessageSender`.

#### 5.1.2. `bard/bot/types.py`: Core Data Structures

*   **[`bard/bot/types.py`](bard/bot/types.py):** This file defines the essential data structures used throughout the bot's operations. It contains dataclasses like `ParsedMessageContext` and `VideoMetadata`, which provide structured, type-safe containers for passing complex data between different services. By centralizing these core types, it ensures consistency and improves code readability across the application.

#### 5.1.3. `bard/bot/core`: Core Orchestration

This sub-package contains the central components that orchestrate the message processing workflow.

*   **[`bard/bot/core/container.py`](bard/bot/core/container.py):** This file implements a dependency injection (DI) container responsible for instantiating and wiring together all the major services the bot uses, such as the `MessageParser`, `AIConversation`, and `MessageSender`. It ensures that services are created as singletons, promoting efficient resource use and consistent state management across the application. The container simplifies the management of dependencies, making the system more modular and easier to maintain.

*   **[`bard/bot/core/coordinator.py`](bard/bot/core/coordinator.py):** The `Coordinator` is the master conductor for processing a single Discord message. Its primary role is to orchestrate the high-level workflow, ensuring a smooth and sequential process from message reception to final reply. It does not perform heavy lifting itself but instead delegates tasks to specialized components. The `process` method is the entry point for this workflow:
    1.  It begins by calling the [`MessageParser`](bard/bot/message/parser.py) to analyze the incoming message, extract its content, and gather multimodal context (text, attachments, replies).
    2.  The resulting parsed context is then passed to the [`AIConversation`](bard/ai/chat/conversation.py) service, which runs the core generative AI logic to produce a response.
    3.  The resulting AI response is then passed to the [`MessageSender`](bard/bot/message/sender.py), which orchestrates the final delivery to Discord, delegating tasks like message splitting, threading, and voice message handling to specialized managers.
    4.  Finally, it uses the [`ReactionManager`](bard/bot/message/reactions.py) to add any relevant tool-use emojis to the bot's message and to handle reaction-based events like retries.

    This coordinated delegation ensures a clean separation of concerns, where each component has a distinct and well-defined responsibility. In the event of a `google.api_core.exceptions.ServerError` (e.g., when the model is overloaded), the `Coordinator` gracefully handles the exception by sending a user-friendly error message and adding a retry reaction, ensuring a consistent user experience.

*   **[`bard/bot/core/handlers.py`](bard/bot/core/handlers.py):** This file defines the `BotHandlers` class, which is implemented as a `commands.Cog`. It serves as the raw entry point for all incoming `discord.py` events. True to its name, this class acts purely as a handler and dispatcher, containing no business logic itself. It immediately delegates events to the appropriate specialized services. For instance, new messages are passed to the `TaskLifecycleManager` to initiate the main processing workflow via the `Coordinator`, while other events like message edits, deletions, or reactions are forwarded directly to the `DiscordEventHandler`. The `on_ready` method has the specific responsibility of setting the bot's user ID in other services and delegating presence updates to the `PresenceManager`.

#### 5.1.4. `bard/bot/lifecycle`: Lifecycle Event Management

This sub-package is responsible for managing the bot's response to Discord events that affect the state of in-flight message processing tasks.

*   **[`bard/bot/lifecycle/events.py`](bard/bot/lifecycle/events.py):** The `DiscordEventHandler` class is the core of this package. It contains the specific business logic for handling events that can modify an ongoing task, such as message edits, deletions, and specific user reactions. It works in close coordination with the `TaskLifecycleManager` to orchestrate the cancellation, reprocessing, or cleanup of tasks. For example:
    *   `handle_edit`: When a user edits a message, this handler cancels the existing processing task and starts a new one with the updated content.
    *   `handle_delete`: If a user deletes a message being processed, this handler cancels the task and removes all associated bot responses.
    *   `handle_retry_reaction`: Listens for the retry emoji (`🔄`) on a bot message. If the reaction is from the original user, it triggers the `TaskLifecycleManager` to re-process the initial prompt.
    *   `handle_cancel_reaction`: Listens for the cancel emoji (`🚫`) on a user's message. If the bot is processing that message, it cancels the task.

*   **[`bard/bot/lifecycle/presence.py`](bard/bot/lifecycle/presence.py):** The `PresenceManager` class is responsible for setting the bot's Discord presence upon startup. It reads the desired activity type (e.g., "Playing", "Watching") and status text directly from the [`config.py`](config.py) file. This provides a simple and direct way to configure the bot's appearance in the server member list without requiring code changes.

#### 5.1.5. `bard/bot/message`: Message Content and Delivery

This sub-package is responsible for parsing incoming messages, formatting and sending outgoing responses, and managing other message-related interactions.

*   **[`bard/bot/message/parser.py`](bard/bot/message/parser.py):** The `MessageParser` is a critical component responsible for the initial processing of all incoming Discord messages. It transforms a raw `discord.Message` object into a structured `ParsedMessageContext` dataclass, preparing the data for the AI. Its key responsibilities include:
    *   **Reply Chain Processing:** It delegates to the [`ReplyChainConstructor`](bard/ai/context/replies.py) to recursively trace a conversation's reply chain, gathering text and attachments from previous messages to provide a complete conversational context.
    *   **URL and Attachment Processing:** It extracts all URLs from the message content and reply chain, delegating their processing to the `ScrapingOrchestrator`. All scraped URLs, even those without extracted text, are passed to the AI model for full context. It also gathers all direct attachments from the current message and the reply chain.
    *   **Contextualization:** It creates a detailed `DiscordContext` object containing environmental information like the channel, server, and present users.
    *   **Data Structuring:** It assembles all the extracted information into the `ParsedMessageContext`, a clean and organized dataclass that serves as the single source of truth for the `AIConversation` service.

*   **[`bard/bot/message/sender.py`](bard/bot/message/sender.py):** The `MessageSender` class serves as the final step in the bot's response pipeline, responsible for all outbound communication with Discord. Its core responsibility is to take the generated content (text, audio, images) and deliver it to the correct channel. To adhere to the single-responsibility principle, it delegates specialized sending tasks to other components:
    *   It uses the `ThreadManager` to create new threads for long, text-only responses, keeping channels clean.
    *   It relies on the `VoiceMessageSender` to handle the complexities of sending native Discord voice messages.
    *   It interfaces with the `MessageManager` to delete old messages when a response is being edited or retried.
    *   It leverages a utility for creating temporary files to manage attachments for images, code, and audio fallbacks.

    By orchestrating these specialized handlers, the `MessageSender` ensures that the final message is formatted and delivered correctly, whether as a simple text reply, a multi-message thread, or a native voice message.

*   **[`bard/bot/message/voice.py`](bard/bot/message/voice.py):** The `VoiceMessageSender` is a specialized service dedicated to sending native Discord voice messages. Because `discord.py` does not support this feature directly, this class interacts with the Discord API at a low level to achieve the desired result. The process involves three main stages:
    1.  **Attachment Registration:** It first makes an API request to register a new attachment (`voice_message.ogg`) and receives a unique `upload_url`.
    2.  **File Upload:** It then uploads the raw OGG audio data to this `upload_url`.
    3.  **Message Send:** Finally, it sends the actual message to the channel, including a special `flags` value and an attachment payload that references the uploaded file. This payload also includes metadata like the audio duration and a visual waveform, which instructs the Discord client to render the message as a voice message.

    This encapsulation of direct API calls keeps the complex and unofficial process of sending voice messages isolated from the rest of the message-sending logic, allowing the [`MessageSender`](bard/bot/message/sender.py) to simply delegate the task without needing to know the underlying implementation details.

*   **[`bard/bot/message/manager.py`](bard/bot/message/manager.py):** The `MessageManager` is a focused service that handles the direct execution of message-related actions, such as deletion and reaction removal. While other components like the `TaskLifecycleManager` track the *state* of in-flight responses, they delegate the final, practical actions to the `MessageManager`. For example, when a response is cancelled or retried, the `MessageManager` is called to reliably delete the original bot message. This creates a clean separation of concerns, where this manager is the sole authority on interacting with Discord's message objects, complete with robust error handling for API exceptions.

*   **[`bard/bot/message/threading.py`](bard/bot/message/threading.py):** The `ThreadManager` is responsible for handling long, text-only bot responses in a way that avoids cluttering channels. When a response exceeds Discord's character limit, this service automatically creates a new thread to contain the full message. Its process is as follows:
    *   It sends the first sentence of the response as a standard reply in the original channel. This provides an immediate, concise answer.
    *   It then creates a new thread attached to that initial reply.
    *   The remainder of the long response is then sent as one or more messages within the newly created thread.
    *   Crucially, it initiates an asynchronous background task to generate a descriptive title for the thread. It uses the `ThreadTitler` service to create a title based on the full content of the response, replacing the default "Continuation of your request..." placeholder. This ensures that threads are clearly and contextually named, making them easy to identify in the channel list.

*   **[`bard/bot/message/reactions.py`](bard/bot/message/reactions.py):** The `ReactionManager` is a specialized service responsible for managing all emoji reactions on the bot's messages. This simple but important component provides immediate visual feedback to the user. Its duties include adding the retry emoji (`🔄`) to all responses, placing tool-use emojis (e.g., `🧠`, `🌐`) on messages where a tool was invoked, and handling the removal of reactions when necessary. This centralized approach ensures that reaction logic is consistent and decoupled from other services like the `Coordinator` or `MessageSender`.

### 5.2. The `bard/ai` Package

The `bard/ai` package encapsulates all logic related to interacting with the Gemini AI model. It is responsible for building prompts, and handling the final AI response.

#### 5.2.1. `bard/ai/types.py`: AI Data Structures

*   **[`bard/ai/types.py`](bard/ai/types.py):** This file defines the `FinalAIResponse` dataclass, which is a structured representation of the AI's output. It consolidates the text content, any generated media, and tool usage indicators into a single, predictable object that can be easily passed to the `MessageSender`.

### 5.3. The `bard/tools` Package

The `bard/tools` package is responsible for defining the structure and implementation of all external tools that the AI can use. This package is central to the bot's extensible functionality, allowing it to perform actions beyond simple text generation.

#### 5.3.1. `bard/tools/base.py`: The Foundation for Tools

*   **[`bard/tools/base.py`](bard/tools/base.py):** This file lays the architectural foundation for all tools in the system. It ensures that every tool adheres to a consistent and predictable structure, making them easier to develop, maintain, and integrate.

    *   **`BaseTool` (Abstract Class):** This is the abstract base class that all concrete tools must inherit from. It establishes a strict contract by requiring the implementation of two key methods:
        *   `get_function_declarations()`: This method must return a list of `FunctionDeclaration` objects. These declarations are crucial as they inform the Gemini model about the tool's capabilities, including its name, purpose, and the parameters it accepts.
        *   `execute_tool()`: This method contains the actual logic that is executed when the AI decides to use the tool. It receives the function name and arguments from the model and performs the corresponding action.

    *   **`ToolContext` (Dataclass):** This class acts as a dependency injection container, providing tools with access to shared resources and context-specific data. It holds instances of core services like the `GeminiCore`, `AttachmentProcessor`, and configuration settings. Crucially, it validates that all injected services conform to their required `Protocol` interfaces, ensuring type safety and consistent behavior across the application.

    *   **Protocols (`...Protocol`):** The file defines several `@runtime_checkable` protocols, such as `GeminiCoreProtocol` and `AttachmentProcessorProtocol`. These protocols define the explicit methods that a service must implement to be used by a tool. This use of structural subtyping decouples the tools from concrete service implementations, allowing for greater flexibility and easier testing. For example, a mock `GeminiCore` can be used during tests as long as it implements the methods defined in `GeminiCoreProtocol`.

By providing this robust and standardized base, [`bard/tools/base.py`](bard/tools/base.py) ensures that the tool ecosystem is modular, predictable, and easily extensible.

#### 5.3.2. `bard/tools/registry.py`: Dynamic Tool Management

*   **[`bard/tools/registry.py`](bard/tools/registry.py):** The `ToolRegistry` class acts as the central manager for all tools available to the AI. Its primary responsibility is to dynamically discover, load, and provide access to the bot's full range of capabilities. During initialization, it systematically scans the `bard/tools/` directory for any Python files that contain classes inheriting from `BaseTool`. It then instantiates each of these tool classes, injecting a shared `ToolContext` that provides access to essential services like the `GeminiCore` and application configuration. By maintaining a comprehensive registry of all tool functions, it enables the `AIConversation` to seamlessly integrate them into the Gemini model's function-calling capabilities, making the entire toolset available for the AI to use.

### 5.4. The `bard/util` Package

The `bard/util` package provides a collection of utility modules that offer shared, low-level functionalities used across the application. These utilities handle common tasks such as data parsing, media processing, and system-level operations, promoting code reuse and maintaining a clean separation of concerns.

#### 5.4.1. `bard/util/system`: System-level Utilities

This sub-package contains modules related to system-level tasks, such as managing asynchronous operations and file system interactions.

*   **[`bard/util/system/lifecycle.py`](bard/util/system/lifecycle.py):** The `TaskLifecycleManager` class is a crucial component for managing the entire lifecycle of asynchronous message processing tasks. Its primary responsibility is to handle the creation, cancellation, and monitoring of `asyncio.Task` instances for each message processing run. When a new message requires processing, this manager creates a task and tracks it. If the same message is edited, it cancels the old task and starts a new one, ensuring that only one active task exists per message.

    A key architectural feature is its relationship with the `Coordinator`. The `TaskLifecycleManager` needs the `Coordinator` to start the processing workflow, but the `Coordinator` may also depend on services that need the manager. To solve this circular dependency, the `Coordinator` instance is injected *after* initialization via a property setter. This allows both components to be instantiated without a deadlock and then wired together. The manager also ensures robust operation by using a `done_callback` to clean up completed or cancelled tasks, log any unhandled exceptions, and remove the `🚫` cancel reaction from the original message, preventing resource leaks and providing clear error visibility.

*   **[`bard/util/system/files.py`](bard/util/system/files.py):** This module provides shared utilities for creating and managing temporary files. The `create_temp_file` async context manager is used across different parts of the application for handling attachments (like generated images or code files) and other file-based operations, ensuring that temporary files are reliably cleaned up after use.

#### 5.4.2. `bard/util/media`: Media Processing Utilities

This sub-package provides a suite of tools for handling various media-related tasks, from MIME type detection to complex audio and video processing.

*   **[`bard/util/media/media.py`](bard/util/media/media.py):** This module contains general-purpose media utilities. The `MimeDetector` class uses the `python-magic` library to reliably determine the MIME type of binary data and guess the appropriate file extension. It also includes a function to extract all URLs from a block of text.

*   **[`bard/util/media/ffmpeg.py`](bard/util/media/ffmpeg.py):** The `FFmpegWrapper` class provides a robust, asynchronous interface for executing FFmpeg commands. It is used for all audio and video conversions, such as converting raw PCM audio from the TTS tool into the OGG Opus format required by Discord. It includes timeout handling and detailed error logging to ensure stability.

*   **[`bard/util/media/video.py`](bard/util/media/video.py):** The `VideoProcessor` class handles all video-related operations. It uses `yt-dlp` to extract comprehensive metadata from video URLs and to retrieve direct streamable URLs for media content. It also orchestrates the use of the `FFmpegWrapper` to stream video content, which can then be passed to the AI for analysis.
