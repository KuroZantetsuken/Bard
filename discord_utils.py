import tempfile
import re
import os
import magic
import logging
import io
import discord
import base64
import asyncio
import aiohttp
from typing import List as TypingList, Tuple, Optional
from google.genai import types as gemini_types
from config import Config
logger = logging.getLogger("Bard")
class MimeDetector:
    @classmethod
    def detect(cls, data: bytes) -> str:
        try:
            mime_type = magic.from_buffer(data, mime=True)
            if mime_type:
                return mime_type
            else:
                logger.warning("🔍 python-magic returned an empty MIME type. Defaulting to octet-stream.")
                return 'application/octet-stream'
        except ImportError:
            logger.error("❌ python-magic library is not installed or libmagic is missing. "
                         "Falling back to 'application/octet-stream'.")
            return 'application/octet-stream'
        except magic.MagicException as e:
            logger.error(f"❌ python-magic encountered an error (e.g., magic file not found): {e}. "
                         "Falling back to 'application/octet-stream'.")
            return 'application/octet-stream'
        except Exception as e:
            logger.error(f"❌ Unexpected error during MIME detection with python-magic: {e}. "
                         "Falling back to 'application/octet-stream'.", exc_info=True)
            return 'application/octet-stream'
class YouTubeProcessor:
    PATTERNS = [
        re.compile(r'https?://(?:www\.)?youtube\.com/watch\?v=([\w-]+)(?:&\S+)?', re.IGNORECASE),
        re.compile(r'https?://youtu\.be/([\w-]+)(?:\?\S+)?', re.IGNORECASE),
        re.compile(r'https?://(?:www\.)?youtube\.com/embed/([\w-]+)(?:\?\S+)?', re.IGNORECASE),
        re.compile(r'https?://(?:www\.)?youtube\.com/v/([\w-]+)(?:\?\S+)?', re.IGNORECASE),
        re.compile(r'https?://(?:www\.)?youtube\.com/shorts/([\w-]+)(?:\?\S+)?', re.IGNORECASE),
    ]
    @classmethod
    def extract_urls(cls, text: str) -> TypingList[str]:
        found_urls = []
        for pattern in cls.PATTERNS:
            matches = pattern.finditer(text)
            for match in matches:
                found_urls.append(match.group(0))
        return list(set(found_urls))
    @classmethod
    def process_content(cls, content: str) -> Tuple[str, TypingList[gemini_types.Part]]:
        urls = cls.extract_urls(content)
        if not urls:
            return content, []
        youtube_parts = []
        for url in urls:
            try:
                youtube_parts.append(gemini_types.Part(
                    file_data=gemini_types.FileData(mime_type="video/youtube", file_uri=url)
                ))
            except Exception as e:
                logger.error(f"❌ Error creating FileData for YouTube URL.\nURL:\n{url}\nError:\n{e}", exc_info=True)
        cleaned_content = content
        for url in urls:
            cleaned_content = cleaned_content.replace(url, "")
        cleaned_content = re.sub(r'\s+', ' ', cleaned_content).strip()
        if youtube_parts:
            logger.info(f"🎬 Identified {len(youtube_parts)} YouTube video link(s) for model processing.")
        return cleaned_content, youtube_parts
class MessageSender:
    @staticmethod
    async def _send_text_reply(message_to_reply_to: discord.Message, text_content: str) -> Optional[discord.Message]:
        primary_sent_message = None
        if not text_content or not text_content.strip():
            text_content = "I processed your request but have no further text to add."
        if len(text_content) > Config.MAX_MESSAGE_LENGTH:
            chunks = []
            current_chunk = ""
            paragraphs = text_content.split('\n\n')
            for i, paragraph in enumerate(paragraphs):
                paragraph_to_add = paragraph + ('\n\n' if i < len(paragraphs) - 1 else '')
                if len(current_chunk) + len(paragraph_to_add) <= Config.MAX_MESSAGE_LENGTH:
                    current_chunk += paragraph_to_add
                else:
                    if current_chunk:
                        chunks.append(current_chunk.strip())
                        current_chunk = ""
                    if len(paragraph_to_add) > Config.MAX_MESSAGE_LENGTH:
                        for k in range(0, len(paragraph_to_add), Config.MAX_MESSAGE_LENGTH):
                            chunks.append(paragraph_to_add[k:k+Config.MAX_MESSAGE_LENGTH])
                    else:
                        current_chunk = paragraph_to_add
            if current_chunk.strip():
                chunks.append(current_chunk.strip())
            if not chunks : chunks = [text_content[:Config.MAX_MESSAGE_LENGTH]]
            for i, chunk in enumerate(chunks):
                try:
                    if i == 0:
                        sent_msg = await message_to_reply_to.reply(chunk)
                        if not primary_sent_message: primary_sent_message = sent_msg
                    else:
                        await message_to_reply_to.channel.send(chunk)
                except discord.HTTPException as e:
                    logger.error(f"❌ Failed to send text chunk {i+1}/{len(chunks)}. Error: {e}", exc_info=True)
                    if i == 0:
                        try:
                            sent_msg = await message_to_reply_to.channel.send(chunk)
                            if not primary_sent_message: primary_sent_message = sent_msg
                        except discord.HTTPException as e_chan:
                            logger.error(f"❌ Failed to send first chunk to channel directly. Error: {e_chan}", exc_info=True)
                            return None
            if primary_sent_message:
                 logger.info(f"📤 Sent multi-part text reply. First part ID: {primary_sent_message.id}")
        else:
            try:
                sent_msg = await message_to_reply_to.reply(text_content)
                primary_sent_message = sent_msg
            except discord.HTTPException as e:
                logger.error(f"❌ Failed to send reply. Attempting to send to channel directly.\nError:\n{e}", exc_info=True)
                try:
                    sent_msg = await message_to_reply_to.channel.send(text_content)
                    primary_sent_message = sent_msg
                except discord.HTTPException as e_chan:
                    logger.error(f"❌ Failed to send to channel directly.\nError:\n{e_chan}", exc_info=True)
                    return None
        if primary_sent_message and len(text_content) <= Config.MAX_MESSAGE_LENGTH:
            logger.info(f"📤 Sent text reply:\n{text_content}")
        return primary_sent_message
    @staticmethod
    async def send(
        message_to_reply_to: discord.Message,
        text_content: Optional[str],
        audio_data: Optional[bytes] = None,
        duration_secs: float = 0.0,
        waveform_b64: str = Config.WAVEFORM_PLACEHOLDER,
        existing_bot_message_to_edit: Optional[discord.Message] = None
    ) -> Optional[discord.Message]:
        """
        Sends a reply. Text is sent first (if any). Audio is then sent as native voice (if any).
        If native voice fails, audio is sent as a file attachment.
        Returns the 'primary' message (text message if sent, otherwise the voice/audio file message).
        """
        primary_response_message: Optional[discord.Message] = None
        if existing_bot_message_to_edit:
            can_safely_edit = (
                text_content and not audio_data and
                not existing_bot_message_to_edit.attachments and
                not (existing_bot_message_to_edit.flags and existing_bot_message_to_edit.flags.voice)
            )
            if can_safely_edit:
                try:
                    await existing_bot_message_to_edit.edit(content=text_content[:Config.MAX_MESSAGE_LENGTH])
                    logger.info(f"✏️ Edited existing bot message with text. ID: {existing_bot_message_to_edit.id}")
                    return existing_bot_message_to_edit
                except discord.HTTPException as e:
                    logger.warning(f"⚠️ Failed to edit text-only bot message (ID: {existing_bot_message_to_edit.id}). Error: {e}. Will delete and resend.", exc_info=False)
            try:
                await existing_bot_message_to_edit.delete()
                logger.info(f"🗑️ Deleted old bot message (ID: {existing_bot_message_to_edit.id}) to allow resending.")
            except discord.HTTPException as e_del:
                logger.warning(f"⚠️ Could not delete old bot message (ID: {existing_bot_message_to_edit.id}) for resend. Error: {e_del}", exc_info=False)
        if text_content and text_content.strip():
            primary_response_message = await MessageSender._send_text_reply(message_to_reply_to, text_content)
            if not primary_response_message:
                logger.error("❌ Failed to send text content. Audio sending will still be attempted if audio data is present.")
        if audio_data:
            sent_native_voice_message_obj: Optional[discord.Message] = None
            temp_ogg_file_path_for_upload = None
            try:
                with tempfile.NamedTemporaryFile(suffix='.ogg', delete=False) as temp_audio_file:
                    temp_audio_file.write(audio_data)
                    temp_ogg_file_path_for_upload = temp_audio_file.name
                async with aiohttp.ClientSession() as session:
                    channel_id_str = str(message_to_reply_to.channel.id)
                    upload_slot_api_url = f"https://discord.com/api/v10/channels/{channel_id_str}/attachments"
                    upload_slot_payload = {"files": [{"filename": "voice_message.ogg", "file_size": len(audio_data), "id": "0", "is_clip": False}]}
                    upload_slot_headers = {"Authorization": f"Bot {Config.DISCORD_BOT_TOKEN}", "Content-Type": "application/json"}
                    attachment_metadata = None
                    async with session.post(upload_slot_api_url, json=upload_slot_payload, headers=upload_slot_headers) as resp_slot:
                        if resp_slot.status == 200:
                            resp_slot_json = await resp_slot.json()
                            if resp_slot_json.get("attachments") and len(resp_slot_json["attachments"]) > 0:
                                attachment_metadata = resp_slot_json["attachments"][0]
                            else:
                                raise Exception(f"Invalid attachment slot response: {await resp_slot.text()}")
                        else:
                            raise Exception(f"Failed to get Discord upload slot. Status: {resp_slot.status}, Response: {await resp_slot.text()}")
                    put_url = attachment_metadata["upload_url"]
                    with open(temp_ogg_file_path_for_upload, 'rb') as file_to_put:
                        async with session.put(put_url, data=file_to_put, headers={'Content-Type': 'audio/ogg'}) as resp_put:
                            if resp_put.status != 200:
                                raise Exception(f"Failed to PUT audio to Discord CDN. Status: {resp_put.status}, Response: {await resp_put.text()}")
                    discord_cdn_filename = attachment_metadata["upload_filename"]
                    send_message_api_url = f"https://discord.com/api/v10/channels/{channel_id_str}/messages"
                    send_message_payload = {
                        "content": "",
                        "flags": 8192,
                        "attachments": [{
                            "id": "0",
                            "filename": "voice_message.ogg",
                            "uploaded_filename": discord_cdn_filename,
                            "duration_secs": round(duration_secs, 2),
                            "waveform": waveform_b64
                        }],
                        "message_reference": {"message_id": str(message_to_reply_to.id)},
                        "allowed_mentions": {"parse": [], "replied_user": False}
                    }
                    send_message_headers = {"Authorization": f"Bot {Config.DISCORD_BOT_TOKEN}", "Content-Type": "application/json"}
                    async with session.post(send_message_api_url, json=send_message_payload, headers=send_message_headers) as resp_send:
                        if resp_send.status == 200 or resp_send.status == 201:
                            response_data = await resp_send.json()
                            message_id = response_data.get("id")
                            if message_id:
                                try:
                                    fetched_msg = await message_to_reply_to.channel.fetch_message(message_id)
                                    sent_native_voice_message_obj = fetched_msg
                                    logger.info(f"🎤 Sent native Discord voice message.")
                                except discord.HTTPException as e_fetch:
                                    logger.warning(f"🎤 Sent native voice message (ID: {message_id}), but failed to fetch Message object. Error: {e_fetch}")
                            else:
                                raise Exception("Discord API reported success for voice message but returned no message ID.")
                        else:
                            raise Exception(f"Discord API send voice message failed. Status: {resp_send.status}, Response: {await resp_send.text()}")
            except Exception as e:
                logger.error(f"❌ Error sending native Discord voice message. Will attempt fallback if applicable.\nError:\n{e}", exc_info=True)
            finally:
                if temp_ogg_file_path_for_upload and os.path.exists(temp_ogg_file_path_for_upload):
                    try: os.unlink(temp_ogg_file_path_for_upload)
                    except OSError: pass
            if sent_native_voice_message_obj and not primary_response_message:
                primary_response_message = sent_native_voice_message_obj
            if not sent_native_voice_message_obj:
                logger.info("🎤 Native voice send unsuccessful or unconfirmed, attempting to send audio as file attachment.")
                temp_ogg_path_regular = None
                sent_audio_file_message_fallback: Optional[discord.Message] = None
                try:
                    with tempfile.NamedTemporaryFile(suffix='.ogg', delete=False) as temp_audio_file_fallback:
                        temp_audio_file_fallback.write(audio_data)
                        temp_ogg_path_regular = temp_audio_file_fallback.name
                    discord_file = discord.File(temp_ogg_path_regular, "voice_response.ogg")
                    if primary_response_message:
                        sent_audio_file_message_fallback = await message_to_reply_to.channel.send(file=discord_file)
                    else:
                        sent_audio_file_message_fallback = await message_to_reply_to.reply(file=discord_file)
                    if sent_audio_file_message_fallback:
                        logger.info(f"📎 Sent voice response as .ogg file attachment (fallback). ID: {sent_audio_file_message_fallback.id}")
                        if not primary_response_message:
                            primary_response_message = sent_audio_file_message_fallback
                except discord.HTTPException as e_file:
                    logger.error(f"❌ Failed to send .ogg file as attachment (fallback). Error: {e_file}", exc_info=True)
                except Exception as e_gen_file:
                    logger.error(f"❌ Unexpected error sending .ogg file (fallback). Error: {e_gen_file}", exc_info=True)
                finally:
                    if temp_ogg_path_regular and os.path.exists(temp_ogg_path_regular):
                        try: os.unlink(temp_ogg_path_regular)
                        except OSError: pass
        if not primary_response_message and (text_content or audio_data):
            logger.error("❌ All attempts to send content (text or audio) failed.")
        return primary_response_message
class ReplyChainProcessor:
    @staticmethod
    async def get_chain(message: discord.Message, bot_user_id: int) -> TypingList[dict]:
        """
        Fetches the reply chain for a message.
        Returns a list of dicts, each containing message info.
        The list is ordered from oldest to newest message in the chain.
        """
        chain = []
        current_msg_obj = message
        depth = 0
        processed_ids = set()
        while current_msg_obj and current_msg_obj.id not in processed_ids and depth < Config.MAX_REPLY_DEPTH:
            processed_ids.add(current_msg_obj.id)
            author_role = "User"
            if current_msg_obj.author.bot:
                author_role = "Assistant (You)" if current_msg_obj.author.id == bot_user_id else "Assistant (Other Bot)"
            msg_info = {
                'message_obj': current_msg_obj,
                'author_name': f"{current_msg_obj.author.display_name} (@{current_msg_obj.author.name})",
                'author_id': current_msg_obj.author.id,
                'author_role': author_role,
                'content': current_msg_obj.content,
                'attachments': list(current_msg_obj.attachments),
                'timestamp': current_msg_obj.created_at
            }
            chain.insert(0, msg_info)
            if hasattr(current_msg_obj, 'reference') and current_msg_obj.reference and current_msg_obj.reference.message_id:
                try:
                    if hasattr(current_msg_obj.channel, 'fetch_message'):
                        current_msg_obj = await current_msg_obj.channel.fetch_message(current_msg_obj.reference.message_id)
                        depth += 1
                    else:
                        logger.warning(f"⚠️ Reply chain processing: Channel type {type(current_msg_obj.channel)} does not support fetch_message.")
                        break
                except (discord.NotFound, discord.Forbidden, discord.HTTPException) as e:
                    logger.warning(f"⚠️ Could not fetch replied-to message (ID: {current_msg_obj.reference.message_id}). Chain might be broken. Error: {e}")
                    break
            else:
                break
        return chain
    @staticmethod
    def format_context_for_llm(chain: TypingList[dict], current_message_id: int) -> str:
        """
        Formats the reply chain context for the LLM.
        Excludes the current message itself from the formatted context.
        """
        if len(chain) <= 1:
            return ""
        context_parts = ["[REPLY_CONTEXT:START]"]
        for msg_data in chain:
            if msg_data['message_obj'].id == current_message_id:
                continue
            role_str = msg_data['author_role']
            formatted_line = f"{role_str} ({msg_data['author_name']}, {msg_data['timestamp'].isoformat()}): {msg_data['content']}"
            if msg_data['attachments']:
                attachment_descs = []
                for att in msg_data['attachments']:
                    attachment_descs.append(f"{att.filename}")
                formatted_line += f" [Attachments: {', '.join(attachment_descs)}]"
            context_parts.append(formatted_line)
        if len(context_parts) > 1:
            context_parts.append("[REPLY_CONTEXT:END]")
            return "\n".join(context_parts)
        return ""