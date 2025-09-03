# Bard

A Discord bot powered by Google's Gemini AI, capable of engaging in conversations, processing various media types, generating spoken responses, and more. This bot is currently under development.

## Features
- **Multimodal:** Understands text, images, audio, videos, and documents.
    - Enhanced video understanding for comprehensive analysis.
    - Web page analysis for real-time information access.
- **History:** Short-term memory per server.
    - Only accessible in the respective server, by any user.
    - Not saved locally.
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

### **Prerequisites:**
- Python 3.10+
- FFmpeg
- requirements.txt

### **Clone the Repository:**
    ```bash
    git clone https://github.com/KuroZantetsuken/Bard.git
    cd Bard
    ```

### **Install Dependencies:**
    ```bash
    # Set up a Virtual Environment (Recommended):
    python3 -m venv .venv
    source .venv/bin/activate
    # Install Python Dependencies:
    pip install -r requirements.txt
    ```

### **Configuration:**
- Rename `example.env` to `.env`.
- Open `.env` and fill in the required values:
    - `DISCORD_BOT_TOKEN`: Your Discord bot token.
    - `GEMINI_API_KEY`: Your Gemini API key.
- Edit `prompts/personality.prompt.md` to define the bot's personality.
- `prompts/capabilities.prompt.md` is highly optimized for the bot's capabilities, take care in editing it.
- **Discord Privileged Intents:** Enable Presence Intent and Server Members Intent in the Discord Developer Portal.

## Running the Bot

Once set up, you can run the bot using:

```bash
python main.py
```

## Development

### **Hot Reloading:**
- To streamline development, the bot supports hot-reloading using `watchdog`, automatically restarting when changes are detected in `.py`, `.env`, and `.prompt.md` files.
- To use, ensure `watchdog` is installed (`pip install -r requirements.txt`) and run:
    ```bash
    python3 hotloading.py
    ```
