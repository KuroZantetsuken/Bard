# Project Documentation

This document provides a comprehensive overview of the project, including its structure, key processes, and architecture. It is intended to help new developers get up to speed quickly.

## Project Overview

This project is a sophisticated Discord bot that leverages the power of Google's Gemini AI to provide intelligent and interactive experiences within a Discord server. It is designed to be highly extensible, with a modular architecture that allows for the addition of new features and capabilities. The bot can understand and process multimodal inputs, including text, images, and video, and can perform actions using a variety of tools.

## Key Processes

This section outlines the critical processes for setting up, configuring, and running the application.

### Installation

To install the project's dependencies, use the `requirements.txt` file.

**Command:**
```bash
pip install -r requirements.txt
```

**Expected Output:**
The command will download and install all the Python packages listed in the `requirements.txt` file.

### Browser Setup

This optional step is for pre-configuring the browser with custom settings, extensions, or other preferences. If you skip this, the project will automatically set up its own browser instance, but without any custom configurations.

**Command:**
```bash
python setup_browser.py
```

**Expected Output:**
This script launches a Chromium browser, allowing you to manually configure extensions or settings. When you close the browser, a `data/browser` directory is created, preserving your custom setup. This directory should be preserved for the scraping functionalities to use your custom configuration.

### Configuration

Application configuration is managed through a `.env` file.

**Steps:**

1.  **Create the `.env` file:**
    Copy the `example.env` file to a new file named `.env`.
    ```bash
    cp example.env .env
    ```

2.  **Edit the `.env` file:**
    Open the `.env` file and fill in the required environment variables. At a minimum, you will need to provide:
    *   `DISCORD_BOT_TOKEN`: Your Discord bot token.
    *   `GEMINI_API_KEY`: Your Google Gemini API key.

### Running the Application

To run the bot, execute the main application script.

**Command:**
```bash
python3 src/hotload.py
```

**Expected Output:**
The bot will start with hot-reloading enabled. Any changes to `.py`, `.env`, or `.prompt.md` files will trigger an automatic restart of the bot.

## Logging

The project uses a centralized logging system configured in [`src/log.py`](src/log.py). This system is designed to provide clear, immediate feedback during development and comprehensive, structured data for debugging and analysis.

### Dual-Output Strategy

The logging system employs a dual-output strategy, sending logs to two distinct destinations based on their severity level:

1.  **Console Output (`INFO` level):**
    *   **Purpose:** Provides real-time, human-readable status updates. These logs are intended to give a high-level overview of the application's state.
    *   **Format:** Simple text format: `[HH:MM] - Log message`
    *   **Example:** `[14:22] - Bot connected to Discord.`

2.  **File Output (`DEBUG` level):**
    *   **Purpose:** Captures verbose, machine-parsable debugging information. These logs contain detailed context useful for troubleshooting and post-mortem analysis.
    *   **Format:** Structured JSON, with each log entry on a new line.
    *   **Location:** A new log file is created in the `data/logs/` directory each time the application starts. The filename is timestamped (e.g., `2025-11-08T19-36-00.json`).

### How to Use the Logger

To add logging to any module, obtain a logger instance using the standard Python `logging` library.

#### Getting a Logger Instance

```python
import logging

log = logging.getLogger("Bard")
```

This ensures that the logger is correctly named after the module, which helps in tracing the origin of log messages.

#### Writing Log Messages

Use the appropriate logging level for your message.

**INFO Level Logging (Console)**

Use `log.info()` for general-purpose, informative messages that should be visible on the console.

```python
log.info("Initializing AI services...")
```

**DEBUG Level Logging (File)**

Use `log.debug()` for detailed debugging information. You can pass a dictionary to the `extra` parameter to include structured context in the JSON log.

```python
user_id = 12345
request_data = {"query": "Hello, world!", "source": "web"}

log.debug(
    "Processing incoming user request.",
    extra={"extra_data": {"user_id": user_id, "request": request_data}}
)
```

The `extra_data` dictionary will be automatically included in the JSON output, making it easy to query and analyze specific fields.

When logging complex objects, ensure they are converted to a JSON-serializable format, such as a dictionary. For example, `discord.Intents` objects are logged by converting them to a dictionary:

```python
log.debug("Discord intents configured.", extra={"data": dict(intents)})
```

## Request Lifecycle Management

The project features a robust, decoupled architecture for managing user requests, retries, and cancellations. This system is centered around a suite of specialized components that work together to ensure reliability and a clean separation of concerns.

### Core Components

*   **`RequestManager` ([`src/bot/core/lifecycle.py`](src/bot/core/lifecycle.py)):** The central component that creates, tracks, and manages the state of all user requests. It is the single source of truth for request status and is responsible for initiating the cancellation process.
*   **`Request` Data Class ([`src/bot/core/lifecycle.py`](src/bot/core/lifecycle.py)):** A simple data class holding all information about a single user request, including its unique ID, state (`RequestState`), and associated data.
*   **`RequestState` Enum ([`src/bot/core/lifecycle.py`](src/bot/core/lifecycle.py)):** An enum defining the possible states of a request: `PENDING`, `PROCESSING`, `DONE`, `CANCELLED`, and `ERROR`.
*   **`ReactionManager` ([`src/bot/message/reactions.py`](src/bot/message/reactions.py)):** A dedicated service that handles all UI feedback related to the request lifecycle. It is responsible for adding, removing, and clearing reactions on user and bot messages to reflect the current state of a request (e.g., adding a "cancel" emoji on creation, or a "retry" emoji on cancellation).
*   **`TypingManager` ([`src/bot/core/typing.py`](src/bot/core/typing.py)):** A component that manages the bot's typing indicator. It ensures the indicator is reliably started and stopped, even when requests are cancelled.
*   **`Coordinator` ([`src/bot/core/coordinator.py`](src/bot/core/coordinator.py)):** The main processing logic that orchestrates the AI response generation. It accepts a `Request` object and periodically checks its `state` to gracefully halt processing if it becomes `CANCELLED`.
*   **`DiscordEventHandler` ([`src/bot/core/events.py`](src/bot/core/events.py)):** The event handler for Discord events (message edits, reactions) that interacts with the `RequestManager` to initiate, cancel, or retry requests.

### Request Processing Flow

1.  A user action (e.g., sending a message) triggers an event in `DiscordEventHandler`.
2.  The event handler creates a new `Request` object via the `RequestManager`.
3.  The `ReactionManager` is notified of the new request and adds the "cancel" emoji to the user's message.
4.  The `TypingManager` starts the typing indicator in the channel.
5.  A new `asyncio.Task` is created to run the `Coordinator.process()` method, passing the `Request` object.
6.  The `RequestManager` associates the task with the request.
7.  The `Coordinator` updates the request's state to `PROCESSING` and begins its work.
8.  Throughout its execution, the `Coordinator` checks if `request.state` is `CANCELLED` at key checkpoints. If it is, it stops processing immediately.
9.  Upon successful completion, an error, or cancellation, the `TypingManager` stops the typing indicator.
10. The `Coordinator` notifies the `ReactionManager`, which then updates the message reactions accordingly (e.g., adding tool or retry emojis).

### Cancellation and Retry Flows

#### Request Cancellation

Users can cancel a request in two ways:

1.  **Message Edit:** If a user edits their message, the `DiscordEventHandler` finds the corresponding active request and calls `request_manager.cancel_request()`.
2.  **Cancel Emoji:** If a user reacts with the configured "cancel" emoji, the `DiscordEventHandler` performs the same cancellation logic.

In both cases, `request_manager.cancel_request()` does the following:
1.  Sets the request's state to `CANCELLED`.
2.  Stops the typing indicator via the `TypingManager`.
3.  Cancels the running `asyncio.Task` associated with the request.
4.  Calls `reaction_manager.handle_request_cancellation()`, which removes all reactions from the user's message and the bot's response (if any) and adds a "retry" emoji to the user's original message.

#### Request Retry

Users can retry a request in two ways:
1.  Reacting with the "retry" emoji on a bot's response message (for completed or failed requests).
2.  Reacting with the "retry" emoji on their own message (after a cancellation).

In both scenarios, the `DiscordEventHandler` detects the reaction, identifies the original message, and starts a new request flow.

This decoupled design ensures that the system can gracefully handle interruptions and user-initiated changes, with a clear separation between the core processing logic and the UI feedback.

## Project Structure

This section provides a detailed breakdown of the project's files and directories.

### Root Directory

| File/Directory | Purpose |
| :--- | :--- |
| [`.gitignore`](.gitignore) | Specifies intentionally untracked files to be ignored by Git. |
| [`DOCUMENTATION.md`](DOCUMENTATION.md) | The main documentation file for the project, intended to be updated with architectural and implementation details. |
| [`example.env`](example.env) | An example environment file that provides a template for setting up environment-specific configurations. |
| [`LICENSE`](LICENSE) | Contains the software license for the project. |
| [`README.md`](README.md) | The introductory file for the project, typically containing a project overview, installation instructions, and usage examples. |
| [`requirements.txt`](requirements.txt) | Lists the Python packages required to run the project. This file is used by `pip` to install dependencies. |
| [`setup_browser.py`](setup_browser.py) | A script to set up the browser environment required for the project's scraping functionalities. |
| [`src/`](src/) | The main package directory for the project. |

### `src/` Directory

| File/Directory | Purpose |
| :--- | :--- |
| [`hotload.py`](src/hotload.py) | Implements hot-reloading functionality, allowing the application to restart automatically when code changes are detected. |
| [`log.py`](src/log.py) | Implements a centralized logging system with dual-output: a simple console log and a verbose JSON log. |
| [`main.py`](src/main.py) | The main entry point of the application. It initializes and runs the bot. |
| [`settings.py`](src/settings.py) | Manages the application's settings and configurations. |
| [`ai/`](src/ai/) | Contains all modules related to artificial intelligence, including configuration, core logic, and tools. |
| [`bot/`](src/bot/) | Includes modules that define the bot's behavior, event handling, and message processing. |
| [`scraping/`](src/scraping/) | Contains modules for web scraping functionalities. |

### `src/ai/` Directory

| File/Directory | Purpose |
| :--- | :--- |
| [`config.py`](src/ai/config.py) | Holds configuration settings for the AI, such as API keys and model parameters. |
| [`core.py`](src/ai/core.py) | Contains the core logic for the AI, including prompt generation and response parsing. |
| [`types.py`](src/ai/types.py) | Defines custom data types and classes used throughout the AI module. |
| [`chat/`](src/ai/chat/) | Sub-package for managing chat-specific AI functionalities. |
| [`context/`](src/ai/context/) | Sub-package for managing the contextual information used by the AI. |
| [`tools/`](src/ai/tools/) | Sub-package containing tools that the AI can use to perform actions. |

### `src/ai/chat/` Directory

| File/Directory | Purpose |
| :--- | :--- |
| [`conversation.py`](src/ai/chat/conversation.py) | Manages the state and flow of conversations with the AI. |
| [`files.py`](src/ai/chat/files.py) | Handles file-based interactions within a chat context. |
| [`titler.py`](src/ai/chat/titler.py) | Generates titles for chat conversations. |

### `src/ai/context/` Directory

| File/Directory | Purpose |
| :--- | :--- |
| [`dynamic.py`](src/ai/context/dynamic.py) | Manages dynamic context that changes during a conversation. |
| [`prompts.py`](src/ai/context/prompts.py) | Stores and manages the prompts used to interact with the AI. |
| [`replies.py`](src/ai/context/replies.py) | Manages and formats the AI's replies. |
| [`videos.py`](src/ai/context/videos.py) | Manages context related to video content. |

### `src/ai/tools/` Directory

| File/Directory | Purpose |
| :--- | :--- |
| [`base.py`](src/ai/tools/base.py) | Defines the base class and interface for all AI tools. |
| [`code.py`](src/ai/tools/code.py) | A tool for executing and analyzing code. |
| [`diagnose.py`](src/ai/tools/diagnose.py) | A tool for diagnosing issues within the application. |
| [`event.py`](src/ai/tools/event.py) | A tool for creating and managing events. |
| [`image.py`](src/ai/tools/image.py) | A tool for processing and analyzing images. |
| [`memory.py`](src/ai/tools/memory.py) | A tool for managing the AI's memory. |
| [`registry.py`](src/ai/tools/registry.py) | Manages the registration and discovery of AI tools. |
| [`search.py`](src/ai/tools/search.py) | A tool for performing web searches. |
| [`tts.py`](src/ai/tools/tts.py) | A tool for text-to-speech conversion. |

### `src/bot/` Directory

| File/Directory | Purpose |
| :--- | :--- |
| [`bot.py`](src/bot/bot.py) | The main file for the bot, defining its class and core functionalities. |
| [`types.py`](src/bot/types.py) | Defines custom data types and classes used throughout the bot module. |
| [`core/`](src/bot/core/) | Contains the core components of the bot's architecture. |
| [`message/`](src/bot/message/) | Handles all aspects of message processing, from parsing to sending. |

### `src/bot/core/` Directory

| File/Directory | Purpose |
| :--- | :--- |
| [`container.py`](src/bot/core/container.py) | Manages the dependency injection container for the bot. |
| [`coordinator.py`](src/bot/core/coordinator.py) | Coordinates actions and workflows between different parts of the bot. |
| [`events.py`](src/bot/core/events.py) | Defines and manages lifecycle events. |
| [`handlers.py`](src/bot/core/handlers.py) | Defines event handlers for various bot events. |
| [`request_manager.py`](src/bot/core/lifecycle.py) | Manages the lifecycle of user requests, including creation, cancellation, and state tracking. |
| [`presence.py`](src/bot/core/presence.py) | Manages the bot's online presence and status. |
| [`typing.py`](src/bot/core/typing.py) | Manages the bot's typing indicator, ensuring it is reliably started and stopped. |

### `src/bot/message/` Directory

| File/Directory | Purpose |
| :--- | :--- |
| [`manager.py`](src/bot/message/manager.py) | Manages the overall process of handling messages. |
| [`parser.py`](src/bot/message/parser.py) | Parses incoming messages to extract commands and content. |
| [`reactions.py`](src/bot/message/reactions.py) | Manages the bot's reactions to messages. |
| [`sender.py`](src/bot/message/sender.py) | Handles the sending of messages from the bot. |
| [`threading.py`](src/bot/message/threading.py) | Manages message threading and conversations. |
| [`voice.py`](src/bot/message/voice.py) | Handles voice messages and voice channel interactions. |

### `src/scraping/` Directory

| File/Directory | Purpose |
| :--- | :--- |
| [`cache.py`](src/scraping/cache.py) | Implements caching for scraped data to improve performance. |
| [`models.py`](src/scraping/models.py) | Defines data models for the scraped information. |
| [`orchestrator.py`](src/scraping/orchestrator.py) | Orchestrates the scraping process, managing multiple scrapers. |
| [`page.py`](src/scraping/page.py) | Represents a web page and provides methods for interacting with it. |
| [`scraper.py`](src/scraping/scraper.py) | The core scraping logic for extracting data from web pages. |
| [`video.py`](src/scraping/video.py) | Specialized scraping logic for video content. |


## Troubleshooting

This section covers common errors and their solutions.

### `TypeError: Object of type ... is not JSON serializable`

This error occurs when the JSON logger attempts to serialize a complex Python object that it doesn't know how to handle.

**Solution:**

The `JsonFormatter` in `src/log.py` now includes a sophisticated `_sanitize_and_trim` method that recursively handles complex objects to prevent serialization errors. This function will:
*   Traverse nested dictionaries, lists, and objects.
*   Convert non-serializable objects into a string representation of their class and attributes.
*   Explicitly handle `bytes` data to prevent raw byte strings from being logged, replacing them with a summary (e.g., `<bytes data of length: 1.23KB>`).
*   Trim excessively large string or byte values to keep log files clean and readable.
*   Protect against circular references by limiting recursion depth.

While the formatter is now more robust, it is still recommended to log structured, serializable data when possible.

### `KeyError: "Attempt to overwrite '...' in LogRecord"`

This error occurs when passing a dictionary to the `extra` parameter of a logging call with a key that conflicts with a reserved attribute of the `LogRecord` class.

**Solution:**

Avoid using reserved keys in the `extra` dictionary. A comprehensive list of these reserved keys is defined in the `RESERVED_ATTRS` set within the `JsonFormatter` class in [`src/log.py`](src/log.py). When a `KeyError` of this nature occurs, consult this list and rename the conflicting key in your logging call.
