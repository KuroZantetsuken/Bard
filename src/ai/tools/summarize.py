import asyncio
import json
import logging
import os
from datetime import date, datetime
from typing import Any, Dict, List

from google.genai import types

from ai.config import GeminiConfigManager
from ai.tools.base import BaseTool, ToolContext

log = logging.getLogger("Bard")


class SummarizeTool(BaseTool):
    """
    A tool that enables the Gemini model to summarize chat history from Discord.
    """

    tool_emoji = "📜"

    def __init__(self, context: ToolContext):
        """
        Initializes the SummarizeTool.

        Args:
            context: The ToolContext object providing shared resources.
        """
        super().__init__(context=context)

    def get_function_declarations(self) -> List[types.FunctionDeclaration]:
        """
        Returns the function declarations for the `summarize_chat` function.
        """
        return [
            types.FunctionDeclaration(
                name="summarize_chat",
                description=(
                    "Exports and summarizes chat history from the current Discord channel for a specified timeframe. "
                    "Use this tool to answer questions about past conversations, like 'what did we discuss yesterday?' or 'summarize last week'. "
                    "The after and before date must be different. Do not accept requests for future dates."
                ),
                parameters=types.Schema(
                    type=types.Type.OBJECT,
                    properties={
                        "after_date": types.Schema(
                            type=types.Type.STRING,
                            description="Messages after this date will be considered. Can be in 'YYYY-MM-DD' or 'YYYY-MM-DD HH:mm' format.",
                        ),
                        "before_date": types.Schema(
                            type=types.Type.STRING,
                            description="Messages before this date will be considered. Can be in 'YYYY-MM-DD' or 'YYYY-MM-DD HH:mm' format.",
                        ),
                    },
                    required=["after_date", "before_date"],
                ),
            )
        ]

    async def execute_tool(
        self, function_name: str, args: Dict[str, Any], context: ToolContext
    ) -> types.Part:
        """
        Executes the `summarize_chat` function.
        """
        log.debug("Tool arguments", extra={"tool_args": args})
        if function_name != "summarize_chat":
            return types.Part(
                function_response=self.function_response_error(
                    function_name, "Unknown function"
                )
            )

        after_date_str = args.get("after_date")
        before_date_str = args.get("before_date")
        if not after_date_str or not before_date_str:
            return types.Part(
                function_response=self.function_response_error(
                    function_name, "Missing after_date or before_date"
                )
            )

        try:
            after_date = datetime.fromisoformat(after_date_str).date()
            before_date = datetime.fromisoformat(before_date_str).date()
        except ValueError:
            return types.Part(
                function_response=self.function_response_error(
                    function_name,
                    "Invalid date format. Use 'YYYY-MM-DD' or 'YYYY-MM-DD HH:mm'.",
                )
            )

        today = date.today()
        if after_date > today or before_date > today:
            log.warning("Attempted to summarize a future date.")
            return types.Part(
                function_response=self.function_response_error(
                    function_name, "Cannot summarize future dates."
                )
            )

        if not context.channel or not hasattr(context.channel, "id"):
            return types.Part(
                function_response=self.function_response_error(
                    function_name, "Missing channel information"
                )
            )

        channel_id = getattr(context.channel, "id")
        output_path = os.path.join(
            self.context.settings.CACHE_DIR,
            f"{channel_id}_{after_date_str}_{before_date_str}.json",
        )

        try:
            chat_log = None
            if os.path.exists(output_path):
                log.info(f"Cache hit for {output_path}")
                with open(output_path, "r", encoding="utf-8") as f:
                    chat_log = self._parse_chat_log(f.read())
            else:
                command = [
                    self.context.settings.DISCORD_CHAT_EXPORTER_PATH,
                    "export",
                    "-t",
                    self.context.settings.DISCORD_BOT_TOKEN,
                    "-c",
                    str(channel_id),
                    "-f",
                    "Json",
                    "--after",
                    after_date_str,
                    "--before",
                    before_date_str,
                    "-o",
                    output_path,
                ]

                log.debug(f"Executing DiscordChatExporter: {' '.join(command)}")
                process = await asyncio.create_subprocess_exec(
                    *command,
                    stdout=asyncio.subprocess.PIPE,
                    stderr=asyncio.subprocess.PIPE,
                )
                stdout, stderr = await process.communicate()

                if process.returncode != 0:
                    error_message = f"DiscordChatExporter failed with code {process.returncode}: {stderr.decode()}"
                    log.error(error_message)
                    return types.Part(
                        function_response=self.function_response_error(
                            function_name, error_message
                        )
                    )

                log.info(f"Successfully exported chat to {output_path}")
                with open(output_path, "r", encoding="utf-8") as f:
                    chat_log = self._parse_chat_log(f.read())

            if not chat_log:
                return types.Part(
                    function_response=self.function_response_error(
                        function_name, "Failed to read or parse chat log."
                    )
                )

            gemini_core = context.gemini_core
            if not gemini_core:
                return types.Part(
                    function_response=self.function_response_error(
                        function_name, "Missing gemini_core"
                    )
                )

            summarization_config = self._create_summarization_config()
            log.debug(f"Summarization input: {chat_log}")
            summarization_prompt = self._create_summarization_prompt(chat_log)

            log.info("Calling Gemini API for summarization.")
            summarization_response = await gemini_core.generate_content(
                model=self.context.settings.MODEL_ID_SECONDARY,
                contents=summarization_prompt,
                config=summarization_config,
            )
            log.info("Finished calling Gemini API for summarization.")

            summary_text = self._extract_response(summarization_response)

            return types.Part(
                function_response=self.function_response_success(
                    function_name, summary_text
                )
            )

        except Exception as e:
            log.error(
                f"An error occurred during chat summarization: {e}", exc_info=True
            )
            return types.Part(
                function_response=self.function_response_error(
                    function_name, f"An unexpected error occurred: {e}"
                )
            )

    def _create_summarization_config(self) -> types.GenerateContentConfig:
        """
        Creates the Gemini generation configuration for the summarization call.
        """
        safety_settings = GeminiConfigManager.get_base_safety_settings()
        config = types.GenerateContentConfig(
            system_instruction=types.Content(
                parts=[
                    types.Part(
                        text="You are an expert at summarizing chat logs. Provide a concise summary of the following chat log."
                    )
                ],
                role="system",
            ),
            temperature=0.5,
            top_p=0.95,
            max_output_tokens=self.context.settings.MAX_OUTPUT_TOKENS,
            safety_settings=safety_settings,
        )
        return config

    def _create_summarization_prompt(self, chat_log: str) -> List[types.Content]:
        """
        Creates the prompt for the summarization call.
        """
        return [
            types.Content(
                parts=[
                    types.Part(text="Please summarize the following chat log:"),
                    types.Part(text=chat_log),
                ],
                role="user",
            )
        ]

    def _parse_chat_log(self, chat_log_json: str) -> str:
        """
        Parses a JSON chat log and extracts the relevant message content.
        """
        try:
            log_data = json.loads(chat_log_json)

            if "messages" not in log_data or not isinstance(log_data["messages"], list):
                log.warning(
                    "Chat log is missing 'messages' list or is not in the expected format."
                )
                return chat_log_json

            parsed_messages = []
            for message in log_data["messages"]:
                author = message.get("author", {}).get("name", "Unknown")
                nick = message.get("author", {}).get("nickname", "Unknown")
                timestamp = message.get("timestamp", "")
                content = message.get("content", "")
                if content:
                    parsed_messages.append(
                        f"[{timestamp}] {author} ({nick}): {content}"
                    )

            return "\n".join(parsed_messages)

        except json.JSONDecodeError:
            log.warning("Failed to parse chat log as JSON. Returning raw content.")
            return chat_log_json
        except Exception as e:
            log.error(
                f"An unexpected error occurred while parsing chat log: {e}",
                exc_info=True,
            )
            return chat_log_json

    @staticmethod
    def _extract_response(response: Any) -> str:
        """
        Attempts to extract textual content from a Gemini API response or Content object.
        """
        if hasattr(response, "text"):
            extracted_text = response.text
        elif (
            isinstance(response, types.Content)
            and hasattr(response, "parts")
            and response.parts is not None
        ):
            extracted_text = "".join(
                [
                    part.text
                    for part in response.parts
                    if hasattr(part, "text") and part.text is not None
                ]
            )
        elif isinstance(response, str):
            extracted_text = response
        else:
            extracted_text = ""
        return extracted_text
