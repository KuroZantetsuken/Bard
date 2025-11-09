# Bard

A Discord bot powered by Google's Gemini AI, capable of engaging in conversations, processing various media types, generating spoken responses, and more. This bot is currently under development.

## Features
- **Multimodal:** Understands text, images, audio, videos, and documents.
    - Enhanced video understanding for comprehensive analysis.
    - Web page analysis for real-time information access.
- **Function Calling:** Uses Gemini's function calling feature for robust tool usage.
    - **Memory:** Long-term memory per user.
        - Only accessible by the respective user, in any server.
        - Saved locally.
        - Reset with by prompting the bot to forget everything.
    - **Text-to-Speech:** Generate native Discord voice messages. Supports different speech styles.
        - Request using natural language.
    - **Google Search & URL Context:** Access Google Search or web URLs using native tools.
    - **Code Execution:** Generate and run Python code to aid responses.
    - **Discord Event Management:** Create and manage scheduled events directly within Discord.
    - **Image Generation:** Generate new images based on textual descriptions.
    - **Project Diagnosis:** Inspect the bot's own project structure and file contents.
- **Context-Aware:** Understands message reply chains along with any attachments.
- **Dynamic Interaction:** Adapts its responses if the user edits or deletes their messages.
    - Re-evaluates messages if edited or deleted.
    - Injects Discord environment context into prompts for grounded responses.
    - Processes attachments from replied messages for complete understanding.
    - Automatically generates concise, relevant titles for threads created from long responses.

## Usage

### **Interaction Methods:**
- **Direct Messages (DMs):** Responds to every message sent in a direct message channel.
- **Server Channels:** Responds when mentioned (`@<BotName>` or replied to with pinging enabled).

### **Retry a Response:**
- React to the bot's message with the retry emoji `🔄` to regenerate its last response.

### **Cancel a Response:**
- React to your own message with the cancel emoji `🚫` to cancel a response that is currently being generated.

## Setup

This section outlines the critical processes for setting up, configuring, and running the application.

### **Prerequisites:**
- Python 3.10+
- FFmpeg

### **1. Clone the Repository:**
```bash
git clone https://github.com/KuroZantetsuken/Bard.git
cd Bard
```

### **2. Install Dependencies:**
```bash
# Set up a Virtual Environment (Recommended):
python3 -m venv .venv
source .venv/bin/activate
# Install Python Dependencies:
pip install -r requirements.txt
```

### **3. Configuration:**
- Copy `example.env` to a new file named `.env`.
  ```bash
  cp example.env .env
  ```
- Open the `.env` file and fill in the required environment variables:
    - `DISCORD_BOT_TOKEN`: Your Discord bot token.
    - `GEMINI_API_KEY`: Your Google Gemini API key.
- Edit `personality.prompt.md` to define the bot's personality.
- `capabilities.prompt.md` is highly optimized for the bot's capabilities, take care in editing it.
- **Discord Privileged Intents:** Enable Presence Intent and Server Members Intent in the Discord Developer Portal.

### **4. Browser Setup (Optional)**
This optional step is for pre-configuring the browser with custom settings, extensions, or other preferences. If you skip this, the project will automatically set up its own browser instance, but without any custom configurations.
```bash
python setup_browser.py
```
This script launches a Chromium browser, allowing you to manually configure extensions or settings. When you close the browser, a `data/browser` directory is created, preserving your custom setup.

## Running the Bot

To run the bot, execute the main application script.
```bash
python3 src/main.py
```

## Development

### **Hot Reloading:**
To streamline development, the bot supports hot-reloading, which automatically restarts the application when changes are detected in `.py`, `.env`, and `.prompt.md` files.

To run with hot-reloading enabled:
```bash
python3 src/hotload.py
```
