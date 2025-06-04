# Bard

A Discord bot powered by Google's Gemini AI, capable of engaging in conversations, processing various media types, generating spoken responses, and more. This bot is currently under development.

## Features
- **Multimodal:** Understands text, images, audio, videos, and documents.
- **History:** Short-term memory per server.
    - Only accessible in the respective server, by any user.
    - Saved locally.
    - Reset with `!reset @Bot`.
- **Function Calling:** Uses Gemini's function calling feature for robust tool usage.
    - **Memory:** Long-term memory per user.
        - Only accessible by the respective user, in any server.
        - Saved locally.
        - Reset with `!forget @Bot`.
    - **Text-to-Speech:** Can generate native Discord voice messages. Supports different speech styles.
        - Request using natural language.
    - **Google Search & URL Context:** Accesses Google Search or web URLs using native tools.
- **Context-Aware:** Understands message reply chains along with any attachments.
- **Dynamic:** Adapts its responses if the user edits or deletes their messages.

## Usage

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
    pip install -r requirements.txt
    ```

### **Configuration:**
- Rename `example.env` to `.env`.
- Open `.env` and fill in the required values:
    - `DISCORD_BOT_TOKEN`: Your Discord bot token.
    - `GEMINI_API_KEY`: Your Gemini API key.
- Edit `prompts/personality.prompt.md` to define the bot's personality.
- `prompts/capabilities.prompt.md` is highly optimized for the bot's capabilities, including function calling. Take care in editing it.

## Running the Bot

Once set up, you can run the bot using:

```bash
python main.py
```