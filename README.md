# Gemini Discord Bot

A Discord bot powered by Google's Gemini AI, capable of engaging in conversations, processing various media types, generating spoken responses, and more. This bot is currently under development.

## Features

*   **Conversational AI:** Uses Google Gemini models for intelligent chat.
*   **Multimodal Understanding:** Processes text, images, audio, videos (including YouTube links), and PDF documents.
*   **Text-to-Speech (TTS):** Can generate audio responses using Gemini TTS and send them as native Discord voice messages. Supports different speech styles if the AI is prompted to use them.
*   **Google Search & URL Analysis:** Accesses Google Search for current information and can analyze content from web URLs when provided.
*   **Context-Aware:** Understands message reply chains to maintain conversational context.
*   **Attachment Handling:** Processes attachments uploaded by users.
*   **Customizable Persona:** The bot's behavior and initial instructions can be defined in the `system_prompt.md` file.
*   **Reset Functionality:** Users can reset their conversation history with the bot.
*   **Handles Message Edits/Deletions:** Adapts its responses if the user edits or deletes their messages.
*   **Persistent Memory:** Locally saves short term memory containing all relevant context that persist between restarts.

## Setup and Installation

1.  **Prerequisites:**
    *   Python 3.8+.
    *   FFmpeg: For native voice message processing (TTS `wav` to `ogg`).

2.  **Clone the Repository:**
    ```bash
    git clone https://github.com/KuroZantetsuken/Bard.git
    cd Bard
    ```

3.  **Install Dependencies:**
    ```bash
    pip install -r requirements.txt
    ```

4.  **Configure Environment Variables:**
    *   Rename the `example.env` file to `.env`.
    *   Open `.env` with a text editor and fill in the required values:
        *   `DISCORD_BOT_TOKEN`: Your Discord bot token.
        *   `GEMINI_API_KEY`: Your Gemini API key.

5.  **Customize Personality:**
    *   Feel free to edit `prompts/personality.prompt.md`. This is purely for personality, core instructions are found in `prompts/capabilities.prompt.md`.

## Running the Bot

Once set up, you can run the bot using:

```bash
python main.py
```
