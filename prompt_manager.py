import os
import logging
from typing import Optional
from datetime import datetime, timezone
from config import Config
logger = logging.getLogger("Bard")
TOOL_FUNCTIONS_PLACEHOLDER = "[TOOLS]"
class PromptManager:
    def __init__(self, config: Config, tool_registry
                 ):
        self.config = config
        self.tool_registry = tool_registry
        self.combined_system_prompt = self._load_and_prepare_combined_system_prompt()
    def _format_tool_declarations_for_prompt(self) -> str:
        declarations = self.tool_registry.get_all_function_declarations()
        if not declarations:
            return "No tools are currently available."
        prompt_parts = ["# Available Functions",
                        "You have access to the following functions. Call them when appropriate. "
                        "You can request multiple functions one after another if needed. Plan ahead for each function call.\n"]
        for decl in declarations:
            func_str = f"- `{decl.name}("
            params_list = []
            if decl.parameters and decl.parameters.properties:
                for param_name, param_schema in decl.parameters.properties.items():
                    param_type = str(param_schema.type).replace("Type.", "").lower()
                    is_required = param_name in (decl.parameters.required or [])
                    optional_str = "" if is_required else " (optional)"
                    params_list.append(f"{param_name}: {param_type}{optional_str}")
            func_str += ", ".join(params_list)
            func_str += ")`:\n"
            func_str += f"  - {decl.description}"
            if decl.parameters and decl.parameters.properties:
                 for param_name, param_schema in decl.parameters.properties.items():
                    if param_schema.description:
                        is_required = param_name in (decl.parameters.required or [])
                        optional_str = " (optional)" if not is_required and "optional" not in param_schema.description.lower() else ""
                        req_str = "Required. " if is_required else ""
                        func_str += f"\n  - `{param_name}`{optional_str}: {req_str}{param_schema.description}"
            prompt_parts.append(func_str)
        return "\n".join(prompt_parts)
    def _load_and_prepare_combined_system_prompt(self) -> str:
        prompt_contents = []
        prompt_dir = self.config.PROMPT_DIR
        if not os.path.isdir(prompt_dir):
            logger.error(f"❌ Prompt directory not found: {prompt_dir}. Using fallback system prompt.")
            return "You are a helpful AI assistant on Discord. Be concise and helpful."
        prompt_files_ordered = []
        personality_file = os.path.join(prompt_dir, "personality.prompt.md")
        capabilities_file = os.path.join(prompt_dir, "capabilities.prompt.md")
        if os.path.isfile(personality_file):
            prompt_files_ordered.append(personality_file)
        else:
            logger.warning(f"⚠️ Personality prompt file not found: {personality_file}")
        if os.path.isfile(capabilities_file):
            prompt_files_ordered.append(capabilities_file)
        else:
            logger.warning(f"⚠️ Capabilities prompt file not found: {capabilities_file}")
        other_prompt_files = sorted([
            os.path.join(prompt_dir, f) for f in os.listdir(prompt_dir)
            if f.endswith(".prompt.md") and
               os.path.isfile(os.path.join(prompt_dir, f)) and
               os.path.join(prompt_dir, f) not in prompt_files_ordered
        ])
        prompt_files_ordered.extend(other_prompt_files)
        if not prompt_files_ordered:
            logger.warning(f"⚠️ No .prompt.md files found in {prompt_dir}. Using fallback system prompt.")
            return "You are a helpful AI assistant on Discord. Be concise and helpful."
        for filepath in prompt_files_ordered:
            filename = os.path.basename(filepath)
            try:
                with open(filepath, "r", encoding="utf-8") as f:
                    content = f.read().strip()
                if content:
                    if filename == "capabilities.prompt.md":
                        tool_descriptions_str = self._format_tool_declarations_for_prompt()
                        if TOOL_FUNCTIONS_PLACEHOLDER in content:
                            content = content.replace(TOOL_FUNCTIONS_PLACEHOLDER, tool_descriptions_str)
                            logger.info(f"🛠️ Injected dynamic tool descriptions into {filename}.")
                        else:
                            logger.warning(f"⚠️ '{TOOL_FUNCTIONS_PLACEHOLDER}' not found in {filename}. "
                                           f"Tool descriptions may not be correctly placed. Appending tools section.")
                            pass
                    prompt_contents.append(content)
                logger.info(f"📝 Successfully loaded and processed {filepath}.")
            except Exception as e:
                logger.error(f"❌ Error loading prompt from file.\nFilepath: {filepath}\nError:\n{e}", exc_info=True)
        final_prompt = "\n\n".join(prompt_contents).strip()
        if not final_prompt:
            logger.error("❌ All .prompt.md files were empty or failed to load. Using minimal fallback.")
            return "You are a helpful AI assistant on Discord. Be concise and helpful."
        logger.info(f"📝 Successfully combined {len(prompt_contents)} prompt file(s) into the system prompt.")
        return final_prompt
    def get_system_prompt(self) -> str:
        """Returns the fully assembled system prompt."""
        return self.combined_system_prompt
    @staticmethod
    def generate_per_message_metadata_header(message_author_id: int, message_author_name: str, message_author_display_name: str,
                                             guild_name: Optional[str], guild_id: Optional[int],
                                             channel_name: str, channel_id: int,
                                             is_thread: bool, thread_parent_name: Optional[str]) -> str:
        """Generates the metadata header for each message sent to the AI."""
        user_str = f"Display Name: {message_author_display_name}, Username: {message_author_name}, ID: {message_author_id})"
        if guild_id:
            guild_name_str = f"Server: {guild_name}, ID: {guild_id}"
            if is_thread:
                channel_name_str = f"Thread: {thread_parent_name}, Channel: {channel_name}, ID: {channel_id}"
            else:
                channel_name_str = f"Channel: {channel_name}, ID: {channel_id}"
        else:
            guild_name_str = 'Server: N/A (Direct Message)'
            channel_name_str = f"Direct Message with {message_author_display_name} (Channel ID: {channel_id})"
        metadata_content = f"""[DYNAMIC_CONTEXT:START]
Timestamp: {datetime.now(timezone.utc).isoformat()}
{guild_name_str}
{channel_name_str}
{user_str}
[DYNAMIC_CONTEXT:END]"""
        return metadata_content