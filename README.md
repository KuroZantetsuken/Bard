# Bard

A Discord bot powered by Google's Gemini AI, capable of engaging in conversations, processing various media types, generating spoken responses, and more. This bot is currently under development.

## Features
- **Multimodal:** Understands text, images, audio, videos, and documents.
- **Text-to-Speech:** Can generate native Discord voice messages. Supports different speech styles.
    - Prompt using natural language.
- **History:** Short-term memory per server.
    - Only accessible in their respective server.
    - Saved locally.
    - Reset with `!reset @Bot`.
- **Memory:** Long-term memory per user.
    - Only accessible by the respective user, in any server.
    - Saved locally.
    - Reset with `!forget @Bot`.
- **Google Search & URL Context:** Accesses Google Search for current information and can analyze content from URLs.
- **Context-Aware:** Understands message reply chains along with any attachments.
- **Dynamic:** Adapts its responses if the user edits or deletes their messages.

## Usage

### **Prerequisites:**
- Python 3.10+.
- FFmpeg: For native voice message processing (`wav` to `ogg`).

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
- Open `main.py` and edit the variables at the top found in the `Config` class.
- Edit `prompts/personality.prompt.md` to define the bot's personality.
- `prompts/capabilities.prompt.md` is highly optimized for the bot's capabilities. Take care in editing it.

## Running the Bot

Once set up, you can run the bot using:

```bash
python main.py
```
