import re
import logging
import gemini_utils
import discord_utils
import discord
import asyncio
from typing import List as TypingList, Optional, Tuple, Dict, Any
from tool_registry import ToolRegistry, ToolContext
from prompt_manager import PromptManager
from history_manager import ChatHistoryManager, HistoryEntry
from google.genai import types as gemini_types
from google.genai import client as genai_client
from discord.ext import commands
from datetime import datetime, timezone
from config import Config
logger = logging.getLogger("Bard")
active_bot_responses: Dict[int, discord.Message] = {}
gemini_client: Optional[genai_client.Client] = None
chat_history_mgr: Optional[ChatHistoryManager] = None
prompt_mgr: Optional[PromptManager] = None
tool_reg: Optional[ToolRegistry] = None
msg_sender: Optional[discord_utils.MessageSender] = None
reply_chain_proc: Optional[discord_utils.ReplyChainProcessor] = None
yt_proc: Optional[discord_utils.YouTubeProcessor] = None
mime_detector: Optional[discord_utils.MimeDetector] = None
attach_proc: Optional[gemini_utils.AttachmentProcessor] = None
gemini_cfg_mgr: Optional[gemini_utils.GeminiConfigManager] = None
resp_extractor: Optional[gemini_utils.ResponseExtractor] = None
intents = discord.Intents.default()
intents.message_content = True
intents.guilds = True
bot = commands.Bot(command_prefix="!", intents=intents)
class MessageProcessor:
    """Handles processing of incoming Discord messages and interaction with Gemini."""
    @staticmethod
    async def _build_gemini_prompt_parts(
        message: discord.Message,
        cleaned_content: str,
        reply_chain_data: TypingList[Dict[str, Any]]
    ) -> Tuple[TypingList[gemini_types.Part], bool]:
        """
        Constructs the list of parts (metadata, memories, text, files, YouTube) to send to Gemini.
        Returns a tuple: (list_of_parts, is_substantively_empty_beyond_context_and_memory).
        Relies on global/passed instances of utility classes.
        """
        global prompt_mgr, tool_reg, yt_proc, attach_proc, mime_detector, bot
        guild = message.guild
        is_thread = isinstance(message.channel, discord.Thread)
        thread_parent_name = message.channel.parent.name if is_thread and hasattr(message.channel.parent, 'name') else None
        metadata_header_str = prompt_mgr.generate_per_message_metadata_header(
            message_author_id=message.author.id,
            message_author_name=message.author.name,
            message_author_display_name=message.author.display_name,
            guild_name=guild.name if guild else None,
            guild_id=guild.id if guild else None,
            channel_name=message.channel.name if hasattr(message.channel, 'name') else "DM",
            channel_id=message.channel.id,
            is_thread=is_thread,
            thread_parent_name=thread_parent_name
        )
        parts: TypingList[gemini_types.Part] = [gemini_types.Part(text=metadata_header_str)]
        memory_manager_instance = tool_reg.get_memory_manager() if tool_reg else None
        if memory_manager_instance:
            user_memories = await memory_manager_instance.load_memories(message.author.id)
            if user_memories:
                formatted_memories = memory_manager_instance.format_memories_for_llm_prompt(message.author.id, user_memories)
                parts.append(gemini_types.Part(text=formatted_memories))
                logger.info(f"🧠 Injected {len(user_memories)} memories for user {message.author.id}.")
        if reply_chain_data:
            textual_reply_ctx = reply_chain_proc.format_context_for_llm(reply_chain_data, message.id)
            if textual_reply_ctx.strip():
                parts.append(gemini_types.Part(text=textual_reply_ctx))
        if message.reference and message.reference.message_id and len(reply_chain_data) > 1:
            replied_to_msg_data = next((m for m in reversed(reply_chain_data) if m['message_obj'].id == message.reference.message_id), None)
            if replied_to_msg_data and replied_to_msg_data['author_id'] != bot.user.id and replied_to_msg_data['attachments']:
                logger.info(f"📎 Processing {len(replied_to_msg_data['attachments'])} attachment(s) from replied-to message ID: {replied_to_msg_data['message_obj'].id}")
                replied_attachments_gemini_parts = await attach_proc.process_discord_attachments(
                    gemini_client, replied_to_msg_data['attachments'], mime_detector
                )
                parts.extend(p for p in replied_attachments_gemini_parts if p)
        content_after_yt, yt_file_data_parts = yt_proc.process_content(cleaned_content)
        parts.extend(yt_file_data_parts)
        user_message_text_to_add = content_after_yt.strip()
        current_message_has_files = bool(message.attachments or yt_file_data_parts)
        if user_message_text_to_add:
            parts.append(gemini_types.Part(text=f"User Message: {user_message_text_to_add}"))
        elif current_message_has_files:
            parts.append(gemini_types.Part(text="User Message: [See attached files or provided links]"))
        if message.attachments:
            logger.info(f"📎 Processing {len(message.attachments)} attachment(s) from current message ID: {message.id}")
            current_attachments_gemini_parts = await attach_proc.process_discord_attachments(
                gemini_client, list(message.attachments), mime_detector
            )
            parts.extend(p for p in current_attachments_gemini_parts if p)
        final_parts = [p for p in parts if p is not None]
        is_substantively_empty = not (user_message_text_to_add or message.attachments or yt_file_data_parts)
        if not final_parts or (len(final_parts) == 1 and final_parts[0].text == metadata_header_str and is_substantively_empty):
             logger.warning("⚠️ No substantive parts built for Gemini beyond initial metadata. Message may be ignored if no history.")
        return final_parts, is_substantively_empty
    @staticmethod
    async def process(message: discord.Message, bot_message_to_edit: Optional[discord.Message] = None):
        global chat_history_mgr, prompt_mgr, tool_reg, gemini_client, bot
        global msg_sender, reply_chain_proc, resp_extractor, gemini_cfg_mgr
        global active_bot_responses
        content_for_llm = message.content.strip()
        guild_id_for_history = message.guild.id if message.guild else None
        user_id_for_dm_history = message.author.id if guild_id_for_history is None else None
        memory_manager_instance = tool_reg.get_memory_manager() if tool_reg else None
        if message.content.strip().lower().startswith(f"{bot.command_prefix}reset"):
            deleted_msg = "No active chat history found to clear."
            if await chat_history_mgr.delete_history(guild_id_for_history, user_id_for_dm_history):
                deleted_msg = "🧹 Chat history has been cleared!"
            bot_resp = await msg_sender.send(message, deleted_msg, existing_bot_message_to_edit=bot_message_to_edit)
            if bot_resp: active_bot_responses[message.id] = bot_resp
            return
        if message.content.strip().lower().startswith(f"{bot.command_prefix}forget"):
            deleted_msg = "No memories found for you to forget."
            if memory_manager_instance:
                if await memory_manager_instance.delete_all_memories(message.author.id):
                    deleted_msg = f"🧠 All your memories with me have been forgotten, {message.author.display_name}."
            else:
                deleted_msg = "Memory management is currently unavailable."
            bot_resp = await msg_sender.send(message, deleted_msg, existing_bot_message_to_edit=bot_message_to_edit)
            if bot_resp: active_bot_responses[message.id] = bot_resp
            return
        async with message.channel.typing():
            try:
                loaded_history_entries: TypingList[HistoryEntry] = await chat_history_mgr.load_history(guild_id_for_history, user_id_for_dm_history)
                history_for_session_init: TypingList[gemini_types.Content] = [
                    entry.content for entry in loaded_history_entries
                    if entry.content.role in ("user", "model")
                ]
                current_session_history_entries: TypingList[HistoryEntry] = list(loaded_history_entries)
                reply_chain_data = await reply_chain_proc.get_chain(message, bot.user.id)
                gemini_prompt_parts, is_substantively_empty = await MessageProcessor._build_gemini_prompt_parts(
                    message, content_for_llm, reply_chain_data
                )
                if is_substantively_empty and not history_for_session_init:
                    logger.info("💬 Message is empty and no history. Sending default greeting.")
                    bot_resp = await msg_sender.send(message, "Hello! How can I help you today?", existing_bot_message_to_edit=bot_message_to_edit)
                    if bot_resp: active_bot_responses[message.id] = bot_resp
                    return
                if not gemini_prompt_parts:
                    logger.error("❌ No prompt parts were built for Gemini. Aborting.")
                    bot_resp = await msg_sender.send(message, "❌ I couldn't prepare your request.", existing_bot_message_to_edit=bot_message_to_edit)
                    if bot_resp: active_bot_responses[message.id] = bot_resp
                    return
                user_turn_content = gemini_types.Content(role="user", parts=gemini_prompt_parts)
                current_session_history_entries.append(
                    HistoryEntry(timestamp=datetime.now(timezone.utc), content=user_turn_content)
                )
                system_prompt_str = prompt_mgr.get_system_prompt()
                tool_declarations = tool_reg.get_all_function_declarations() if tool_reg else []
                main_gen_config = gemini_cfg_mgr.create_main_config(system_prompt_str, tool_declarations)
                contents_for_gemini_call: TypingList[gemini_types.Content] = []
                if system_prompt_str and not main_gen_config.system_instruction:
                    pass
                contents_for_gemini_call.extend(history_for_session_init)
                contents_for_gemini_call.append(user_turn_content)
                logger.info(f"📥 Message received:\n{message.author.display_name}: {content_for_llm}")
                response_from_gemini = await gemini_client.aio.models.generate_content(
                    model=Config.MODEL_ID,
                    contents=contents_for_gemini_call,
                    config=main_gen_config,
                )
                response_text_parts = []
                function_calls_to_execute = []
                tool_exec_context = ToolContext(
                    gemini_client=gemini_client, config=Config, user_id=message.author.id,
                    original_user_turn_content=user_turn_content,
                    history_for_tooling_call=history_for_session_init,
                    gemini_config_manager=gemini_cfg_mgr, response_extractor=resp_extractor,
                    audio_data=None, audio_duration=None, audio_waveform=None
                )
                if response_from_gemini.candidates and response_from_gemini.candidates[0].content:
                    model_response_content = response_from_gemini.candidates[0].content
                    if model_response_content.role != "model":
                        model_response_content = gemini_types.Content(role="model", parts=model_response_content.parts)
                    current_session_history_entries.append(
                        HistoryEntry(timestamp=datetime.now(timezone.utc), content=model_response_content)
                    )
                    for part in model_response_content.parts:
                        if part.text:
                            response_text_parts.append(part.text)
                        elif part.function_call:
                            function_calls_to_execute.append(part.function_call)
                else:
                    logger.error("❌ No valid content in Gemini's initial response (Main Call).")
                    response_text_parts.append("[Error: AI did not provide an initial response.]")
                if function_calls_to_execute:
                    logger.info(f"⚙️ Model requested {len(function_calls_to_execute)} function call(s). Executing...")
                    function_response_parts_for_gemini: TypingList[gemini_types.Part] = []
                    tool_history_parts_for_session = []
                    for fc_obj in function_calls_to_execute:
                        tool_history_parts_for_session.append(gemini_types.Part(function_call=fc_obj))
                        function_response_part = await tool_reg.execute_function(
                            fc_obj.name, dict(fc_obj.args) if fc_obj.args else {}, tool_exec_context
                        )
                        if function_response_part:
                            function_response_parts_for_gemini.append(function_response_part)
                            tool_history_parts_for_session.append(function_response_part)
                        else:
                            logger.error(f"❌ Execution of function {fc_obj.name} returned None.")
                            err_resp = gemini_types.Part(function_response=gemini_types.FunctionResponse(
                                name=fc_obj.name, response={"success": False, "error": "Tool execution failed to return a response."}
                            ))
                            function_response_parts_for_gemini.append(err_resp)
                            tool_history_parts_for_session.append(err_resp)
                    if tool_history_parts_for_session:
                        current_session_history_entries.append(
                            HistoryEntry(timestamp=datetime.now(timezone.utc),
                                         content=gemini_types.Content(role="tool", parts=tool_history_parts_for_session))
                        )
                    if function_response_parts_for_gemini:
                        logger.info(f"⚙️ Sending {len(function_response_parts_for_gemini)} function execution result(s) back to Gemini.")
                        contents_for_gemini_call.append(model_response_content)
                        contents_for_gemini_call.append(gemini_types.Content(role="tool", parts=function_response_parts_for_gemini))
                        response_after_functions = await gemini_client.aio.models.generate_content(
                            model=Config.MODEL_ID,
                            contents=contents_for_gemini_call,
                            config=main_gen_config
                        )
                        response_text_parts = []
                        final_model_response_content_for_history: Optional[gemini_types.Content] = None
                        if response_after_functions.candidates:
                            candidate = response_after_functions.candidates[0]
                            # Log finish reason and safety ratings for debugging
                            finish_reason_str = candidate.finish_reason.name if hasattr(candidate.finish_reason, 'name') else str(candidate.finish_reason)
                            safety_ratings_str = str(candidate.safety_ratings) if hasattr(candidate, 'safety_ratings') else 'N/A'
                            if candidate.content:
                                model_content_from_candidate = candidate.content
                                parts_for_history = []
                                if hasattr(model_content_from_candidate, 'parts') and model_content_from_candidate.parts is not None:
                                    parts_for_history = list(model_content_from_candidate.parts)
                                final_model_response_content_for_history = gemini_types.Content(
                                    role="model",
                                    parts=parts_for_history
                                )
                                if parts_for_history:
                                    for part in parts_for_history:
                                        if part.text:
                                            response_text_parts.append(part.text)
                                        elif part.function_call:
                                            logger.warning(f"⚠️ Model attempted a function call ({part.function_call.name}) AFTER initial function results. Executing this follow-up.")

                                            follow_up_tool_history_parts = [gemini_types.Part(function_call=part.function_call)]

                                            follow_up_resp_part = await tool_reg.execute_function( # type: ignore
                                                part.function_call.name, dict(part.function_call.args) if part.function_call.args else {}, tool_exec_context
                                            )
                                            if follow_up_resp_part:
                                                follow_up_tool_history_parts.append(follow_up_resp_part)
                                            else:
                                                 follow_up_tool_history_parts.append(gemini_types.Part(function_response=gemini_types.FunctionResponse(
                                                    name=part.function_call.name, response={"success": False, "error": "Follow-up tool execution failed to return a response part."}
                                                )))
                                            current_session_history_entries.append(
                                                HistoryEntry(timestamp=datetime.now(timezone.utc),
                                                            content=gemini_types.Content(role="tool", parts=follow_up_tool_history_parts))
                                            )
                                            logger.info(f"⚙️ Follow-up function call {part.function_call.name} executed. Its result is NOT sent back to LLM in this turn.")
                                else:
                                    logger.info("ℹ️ Response after functions: Model content has no actual parts to process (e.g., after TTS, this is often expected).")
                            else:
                                logger.error("❌ No valid content object in Gemini's response after function execution (candidate.content is None).")
                                response_text_parts.append("[Error: AI did not provide a content object after function results.]")
                        else:
                            logger.error("❌ No candidates in Gemini's response after function execution.")
                            response_text_parts.append("[Error: AI did not provide any candidates after function results.]")
                        if final_model_response_content_for_history:
                             current_session_history_entries.append(
                                HistoryEntry(timestamp=datetime.now(timezone.utc), content=final_model_response_content_for_history)
                            )
                final_text_for_discord = "\n".join(response_text_parts).strip()
                final_audio_data = tool_exec_context.get("audio_data")
                final_audio_duration = tool_exec_context.get("audio_duration", 0.0)
                final_audio_waveform = tool_exec_context.get("audio_waveform", Config.WAVEFORM_PLACEHOLDER)
                if not final_text_for_discord and not final_audio_data:
                    final_text_for_discord = "I processed your request but have no further text or audio to send."
                await chat_history_mgr.save_history(guild_id_for_history, user_id_for_dm_history, current_session_history_entries)
                new_bot_msg = await msg_sender.send(
                    message,
                    final_text_for_discord if final_text_for_discord else None,
                    final_audio_data,
                    final_audio_duration,
                    final_audio_waveform,
                    bot_message_to_edit
                )
                if new_bot_msg:
                    active_bot_responses[message.id] = new_bot_msg
                elif message.id in active_bot_responses:
                    active_bot_responses.pop(message.id, None)
            except gemini_types.StopCandidateException as sce:
                logger.error(f"❌ Gemini API StopCandidateException: {sce}", exc_info=True)
                err_msg = f"❌ The AI stopped responding unexpectedly (Reason: {sce.finish_reason}). Please try again."
                bot_resp = await msg_sender.send(message, err_msg, existing_bot_message_to_edit=bot_message_to_edit)
                if bot_resp: active_bot_responses[message.id] = bot_resp
            except gemini_types.BlockedPromptException as bpe:
                logger.error(f"❌ Gemini API BlockedPromptException: {bpe}", exc_info=True)
                err_msg = "❌ Your request was blocked by the AI's safety filters. Please rephrase."
                bot_resp = await msg_sender.send(message, err_msg, existing_bot_message_to_edit=bot_message_to_edit)
                if bot_resp: active_bot_responses[message.id] = bot_resp
            except Exception as e:
                logger.error(f"❌ Message processing pipeline error for user {message.author.name}: {e}", exc_info=True)
                err_msg = "❌ I encountered an error processing your request."
                bot_resp = await msg_sender.send(message, err_msg, existing_bot_message_to_edit=bot_message_to_edit)
                if bot_resp: active_bot_responses[message.id] = bot_resp
                elif message.id in active_bot_responses and bot_message_to_edit:
                    active_bot_responses.pop(message.id, None)
@bot.event
async def on_ready():
    logger.info(f"🎉 Logged in as {bot.user.name} (ID: {bot.user.id})")
    logger.info(f"🔗 Discord.py Version: {discord.__version__}")
    logger.info(f"🧠 Using Main Gemini Model: {Config.MODEL_ID}")
    logger.info(f"🎤 Using TTS Gemini Model: {Config.MODEL_ID_TTS} with Voice: {Config.VOICE_NAME}")
    if Config.MAX_HISTORY_TURNS > 0:
        logger.info(f"💾 Chat History Max Turns: {Config.MAX_HISTORY_TURNS}")
    else:
        logger.info("💾 Chat History Max Turns: Disabled")
    if Config.MAX_HISTORY_AGE > 0 & Config.MAX_HISTORY_TURNS > 0:
        logger.info(f"💾 Chat History Max Age: {Config.MAX_HISTORY_AGE} minutes")
    elif Config.MAX_HISTORY_TURNS > 0:
        logger.info("💾 Chat History Max Age: Unlimited")
    logger.info(f"🧠 User Memory Max Entries: {Config.MAX_MEMORIES}")
    logger.info(f"🛠️ Tools loaded: {list(tool_reg.tools.keys()) if tool_reg else 'N/A (ToolRegistry not init)'}")
    try:
        activity_name = f"messages | {bot.command_prefix}reset | {bot.command_prefix}forget"
        await bot.change_presence(activity=discord.Activity(type=discord.ActivityType.listening, name=activity_name))
    except Exception as e:
        logger.warning(f"⚠️ Could not set bot presence: {e}")
@bot.event
async def on_message(message: discord.Message):
    if message.author == bot.user or message.author.bot:
        return
    is_dm = isinstance(message.channel, discord.DMChannel)
    is_mentioned = bot.user.mentioned_in(message) if bot.user else False
    is_reply_to_bot = False
    if message.reference and message.reference.message_id:
        try:
            ref_msg = message.reference.cached_message
            if not ref_msg and hasattr(message.channel, 'fetch_message'):
                try:
                    ref_msg = await message.channel.fetch_message(message.reference.message_id)
                except (discord.NotFound, discord.Forbidden, discord.HTTPException):
                    pass
            if ref_msg and ref_msg.author == bot.user:
                is_reply_to_bot = True
        except Exception as e_ref:
            logger.debug(f"🔍 Error checking message reference: {e_ref}")
    is_command = message.content.lower().strip().startswith(f"{bot.command_prefix}reset") or \
                 message.content.lower().strip().startswith(f"{bot.command_prefix}forget")
    if is_dm or is_mentioned or is_reply_to_bot or is_command:
        await MessageProcessor.process(message)
@bot.event
async def on_message_edit(before: discord.Message, after: discord.Message):
    if after.author == bot.user or after.author.bot:
        return
    if before.content == after.content and \
       before.attachments == after.attachments and \
       not before.embeds and after.embeds and \
       not any(e.type == 'gifv' for e in after.embeds):
        return
    is_dm_after = isinstance(after.channel, discord.DMChannel)
    is_mentioned_after = bot.user.mentioned_in(after) if bot.user else False
    is_reply_to_bot_after = False
    if after.reference and after.reference.message_id:
        try:
            ref_msg = after.reference.cached_message
            if not ref_msg and hasattr(after.channel, 'fetch_message'):
                 try: ref_msg = await after.channel.fetch_message(after.reference.message_id)
                 except: pass
            if ref_msg and ref_msg.author == bot.user: is_reply_to_bot_after = True
        except Exception: pass
    is_command_after = after.content.lower().strip().startswith(f"{bot.command_prefix}reset") or \
                       after.content.lower().strip().startswith(f"{bot.command_prefix}forget")
    should_process_after = is_dm_after or is_mentioned_after or is_reply_to_bot_after or is_command_after
    existing_bot_response_msg = active_bot_responses.get(after.id)
    if not should_process_after:
        if existing_bot_response_msg:
            logger.info(f"🗑️ Edited message (ID: {after.id}) no longer qualifies. Deleting previous bot response (ID: {existing_bot_response_msg.id}).")
            try:
                await existing_bot_response_msg.delete()
            except discord.HTTPException as e_del:
                logger.warning(f"⚠️ Could not delete previous bot response {existing_bot_response_msg.id}: {e_del}")
            finally:
                active_bot_responses.pop(after.id, None)
        return
    logger.info(f"📥 Edited message (ID: {after.id}) qualifies and is substantive. Reprocessing.")
    await MessageProcessor.process(after, bot_message_to_edit=existing_bot_response_msg)
@bot.event
async def on_message_delete(message: discord.Message):
    if message.id in active_bot_responses:
        bot_response_to_delete = active_bot_responses.pop(message.id, None)
        if bot_response_to_delete:
            try:
                await bot_response_to_delete.delete()
                logger.info(f"🗑️ Bot response (ID: {bot_response_to_delete.id}) deleted because original user message (ID: {message.id}) was deleted.")
            except discord.HTTPException as e:
                logger.warning(f"⚠️ Failed to delete bot response (ID: {bot_response_to_delete.id}) for deleted user message. Error: {e}")
def validate_env_vars():
    """Validates essential environment variables."""
    if not Config.DISCORD_BOT_TOKEN:
        raise ValueError("DISCORD_BOT_TOKEN not found. Please set it in your .env file or environment.")
    if not Config.GEMINI_API_KEY:
        raise ValueError("GEMINI_API_KEY not found. Please set it in your .env file or environment.")
    logger.info("✅ Environment variables validated.")
def setup_logging_config():
    """Configures logging for the application."""
    for handler in logging.root.handlers[:]:
        logging.root.removeHandler(handler)
    logging.basicConfig(level=logging.WARNING, format='%(asctime)s [%(levelname)s] [%(name)s] %(message)s',
                        handlers=[logging.StreamHandler()], force=True)
    app_logger = logging.getLogger("Bard")
    for handler in app_logger.handlers[:]: app_logger.removeHandler(handler)
    app_logger.setLevel(logging.INFO)
    app_logger.propagate = False
    console_handler = logging.StreamHandler()
    console_formatter = logging.Formatter('%(message)s')
    console_handler.setFormatter(console_formatter)
    app_logger.addHandler(console_handler)
    try:
        file_handler = logging.FileHandler('.log', mode='a', encoding='utf-8')
        detailed_file_formatter = logging.Formatter(
            '%(asctime)s [%(levelname)-5s] [%(name)s:%(module)s:%(funcName)s:%(lineno)d] %(message)s'
        )
        file_handler.setFormatter(detailed_file_formatter)
        app_logger.addHandler(file_handler)
    except Exception as e:
        app_logger.error(f"❌ Failed to set up file logging: {e}")
    logging.getLogger("discord").setLevel(logging.WARNING)
    logging.getLogger("discord.http").setLevel(logging.WARNING)
    logging.getLogger("google.genai").setLevel(logging.WARNING)
    logging.getLogger("httpx").setLevel(logging.WARNING)
    logging.getLogger("httpcore").setLevel(logging.WARNING)
    logging.getLogger("asyncio").setLevel(logging.WARNING)
    app_logger.info("⚙️ Logging configured.")
def initialize_components():
    """Initializes global components like clients and managers."""
    global gemini_client, chat_history_mgr, prompt_mgr, tool_reg
    global msg_sender, reply_chain_proc, yt_proc, mime_detector, attach_proc
    global gemini_cfg_mgr, resp_extractor
    try:
        gemini_client = genai_client.Client(api_key=Config.GEMINI_API_KEY)
        logger.info(f"🤖 Gemini AI Client initialized.")
    except Exception as e:
        logger.critical(f"💥 Failed to initialize Gemini AI Client: {e}", exc_info=True)
        raise
    chat_history_mgr = ChatHistoryManager()
    tool_reg = ToolRegistry(Config())
    prompt_mgr = PromptManager(Config(), tool_reg)
    msg_sender = discord_utils.MessageSender()
    reply_chain_proc = discord_utils.ReplyChainProcessor()
    yt_proc = discord_utils.YouTubeProcessor()
    mime_detector = discord_utils.MimeDetector()
    attach_proc = gemini_utils.AttachmentProcessor()
    gemini_cfg_mgr = gemini_utils.GeminiConfigManager()
    resp_extractor = gemini_utils.ResponseExtractor()
    logger.info("✅ All core components initialized.")
def main_sync():
    """Synchronous main function to setup and run the bot."""
    try:
        setup_logging_config()
        logger.info("🚀 Initializing Gemini Discord Bot...")
        validate_env_vars()
        initialize_components()
        logger.info("📡 Starting Discord bot...")
        bot.run(Config.DISCORD_BOT_TOKEN, log_handler=None)
    except ValueError as ve:
        print(f"Configuration Error: {ve}")
        if logger and logger.handlers: logger.critical(f"💥 Configuration Error: {ve}", exc_info=False)
        return 1
    except discord.LoginFailure as lf:
        log_msg = f"🛑 Discord Login Failed. Check bot token and intents.\nError: {lf}"
        print(log_msg)
        if logger and logger.handlers: logger.critical(log_msg, exc_info=False)
        return 1
    except Exception as e:
        log_msg = f"💥 Fatal error during bot execution: {e}"
        print(log_msg)
        if logger and logger.handlers: logger.critical(log_msg, exc_info=True)
        return 1
    finally:
        if logger and logger.handlers: logger.info("🛑 Bot shutdown sequence initiated.")
    return 0
if __name__ == "__main__":
    exit_code = main_sync()
    final_log_msg_base = "Bot exited"
    if exit_code == 0:
        final_log_msg = f"✅ {final_log_msg_base} gracefully."
    else:
        final_log_msg = f"⚠️ {final_log_msg_base} with error code: {exit_code}."
    if 'logger' in globals() and logger and logger.handlers:
        if exit_code == 0: logger.info(final_log_msg)
        else: logger.warning(final_log_msg)
        logging.shutdown()
    else:
        print(final_log_msg)
