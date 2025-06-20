import aiohttp
import asyncio
import base64
import discord
import io
import json
import logging
import magic
import mimetypes
import os
import re
import tempfile
import yt_dlp
from collections import defaultdict
from config import Config
from gemini_utils import upload_media_bytes_to_file_api
from google.genai import client as genai_client
from google.genai import types as gemini_types
from typing import Dict
from typing import List as TypingList
from typing import Optional
from typing import Tuple
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
    @classmethod
    def get_extension(cls, mime_type: str) -> str:
        """Guesses a file extension for a given MIME type, with a fallback."""
        if not mime_type:
            return '.bin'
        ext = mimetypes.guess_extension(mime_type)
        return ext if ext else '.bin'
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
    def is_youtube_url(cls, url: str) -> bool:
        return any(pattern.match(url) for pattern in cls.PATTERNS)
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
            logger.info(f"🎬 Identified {len(youtube_parts)} YouTube video link(s) for native model processing.")
        return cleaned_content, youtube_parts
class GenericVideoProcessor:
    URL_REGEX = re.compile(r'https?://[^\s/$.?#].[^\s]*', re.IGNORECASE)
    _video_url_cache: Dict[str, gemini_types.FileData] = {}
    _video_url_locks: defaultdict[str, asyncio.Lock] = defaultdict(asyncio.Lock)
    @staticmethod
    def extract_urls(text: str) -> TypingList[str]:
        return GenericVideoProcessor.URL_REGEX.findall(text)
    @classmethod
    async def process_url(
        cls, url: str, prompt_text: str, gemini_client: genai_client.Client
    ) -> Optional[gemini_types.Part]:
        """
        Processes a video from a URL by piping it from yt-dlp to the Gemini API.
        Uses a cache to avoid redundant downloads and uploads.
        """
        cache_key = url
        if cache_key in cls._video_url_cache:
            cached_file_data = cls._video_url_cache[cache_key]
            logger.info(f"🎬 Cache HIT for video {cache_key}. Using URI: {cached_file_data.file_uri}")
            return gemini_types.Part(file_data=cached_file_data)
        lock = cls._video_url_locks[cache_key]
        async with lock:
            if cache_key in cls._video_url_cache:
                cached_file_data = cls._video_url_cache[cache_key]
                logger.info(f"🎬 Cache HIT (after lock) for video {cache_key}. Using URI: {cached_file_data.file_uri}")
                return gemini_types.Part(file_data=cached_file_data)
            logger.info(f"🎬 Cache MISS for {cache_key}. Piping video from yt-dlp to Gemini.")
            ydl_command = [
                'yt-dlp', url,
                '-f', 'worstvideo[ext=mp4]+worstaudio[ext=m4a]/worst[ext=mp4]/worst',
                '-o', '-',
                '--quiet',
            ]
            process = None
            try:
                process = await asyncio.create_subprocess_exec(
                    *ydl_command,
                    stdout=asyncio.subprocess.PIPE,
                    stderr=asyncio.subprocess.PIPE
                )
                video_bytes, stderr_bytes = await process.communicate()
                if process.returncode != 0:
                    logger.error(f"❌ yt-dlp failed for URL {url} with code {process.returncode}.\n"
                                 f"Stderr:\n{stderr_bytes.decode(errors='ignore')}")
                    return None
                if not video_bytes:
                    logger.error(f"❌ yt-dlp ran successfully for URL {url} but produced no output.")
                    return None
                logger.info(f"✅ Successfully streamed {len(video_bytes)} bytes from yt-dlp for URL: {url}")
                display_name = os.path.basename(url.split('?')[0]) or "streamed_video.mp4"
                gemini_part = await upload_media_bytes_to_file_api(
                    gemini_client, video_bytes, display_name, "video/mp4"
                )
                if gemini_part and gemini_part.file_data:
                    cls._video_url_cache[cache_key] = gemini_part.file_data
                    logger.info(f"🎬 Cached Gemini File API data for video key: {cache_key}.")
                return gemini_part
            except FileNotFoundError:
                logger.error("❌ yt-dlp not found. It must be installed and in the system's PATH.")
                return None
            except Exception as e:
                logger.error(f"❌ Unexpected error processing video stream from {url}: {e}", exc_info=True)
                if process and process.returncode is None:
                    process.kill()
                    await process.wait()
                return None
class MessageSender:
    @staticmethod
    async def _send_text_reply(message_to_reply_to: discord.Message, text_content: str, file_to_attach: Optional[discord.File] = None) -> TypingList[discord.Message]:
        sent_messages: TypingList[discord.Message] = []
        if not text_content or not text_content.strip():
            if file_to_attach:
                text_content = ""
            else:
                text_content = "I processed your request but have no further text to add."
        if len(text_content) > Config.MAX_MESSAGE_LENGTH:
            if file_to_attach:
                warning_msg = "\n\n[Warning: Response truncated. The full response was too long to display with an attachment.]"
                text_content = text_content[:Config.MAX_MESSAGE_LENGTH - len(warning_msg)] + warning_msg
                logger.warning("Message with image was truncated as it exceeded MAX_MESSAGE_LENGTH.")
                chunks = [text_content]
            else:
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
                sent_msg_for_chunk = None
                try:
                    file_for_this_turn = file_to_attach if i == 0 else None
                    if i == 0:
                        sent_msg_for_chunk = await message_to_reply_to.reply(chunk, file=file_for_this_turn)
                    else:
                        sent_msg_for_chunk = await message_to_reply_to.channel.send(chunk)
                    if sent_msg_for_chunk:
                        sent_messages.append(sent_msg_for_chunk)
                        try: await sent_msg_for_chunk.add_reaction(Config.RETRY_EMOJI)
                        except discord.HTTPException as e_react: logger.warning(f"⚠️ Could not add retry reaction to message {sent_msg_for_chunk.id}: {e_react}")
                except discord.HTTPException as e:
                    logger.error(f"❌ Failed to send text chunk {i+1}/{len(chunks)}. Error: {e}", exc_info=True)
                    if i == 0:
                        try:
                            sent_msg_for_chunk = await message_to_reply_to.channel.send(chunk, file=file_for_this_turn)
                            if sent_msg_for_chunk:
                                sent_messages.append(sent_msg_for_chunk)
                                try: await sent_msg_for_chunk.add_reaction(Config.RETRY_EMOJI)
                                except discord.HTTPException as e_react: logger.warning(f"⚠️ Could not add retry reaction to message {sent_msg_for_chunk.id}: {e_react}")
                        except discord.HTTPException as e_chan:
                            logger.error(f"❌ Failed to send first chunk to channel directly. Error: {e_chan}", exc_info=True)
                            return sent_messages
            if sent_messages:
                 logger.info(f"📤 Sent multi-part text reply. First part ID: {sent_messages[0].id}, total parts: {len(sent_messages)}.")
        else:
            sent_msg = None
            try:
                sent_msg = await message_to_reply_to.reply(text_content, file=file_to_attach)
            except discord.HTTPException as e:
                logger.error(f"❌ Failed to send reply. Attempting to send to channel directly.\nError:\n{e}", exc_info=True)
                try:
                    sent_msg = await message_to_reply_to.channel.send(text_content, file=file_to_attach)
                except discord.HTTPException as e_chan:
                    logger.error(f"❌ Failed to send to channel directly.\nError:\n{e_chan}", exc_info=True)
            if sent_msg:
                sent_messages.append(sent_msg)
                try: await sent_msg.add_reaction(Config.RETRY_EMOJI)
                except discord.HTTPException as e_react: logger.warning(f"⚠️ Could not add retry reaction to message {sent_msg.id}: {e_react}")
                logger.info(f"📤 Sent text reply:\n{text_content}")
        return sent_messages
    @staticmethod
    async def send(
        message_to_reply_to: discord.Message,
        text_content: Optional[str],
        audio_data: Optional[bytes] = None,
        duration_secs: float = 0.0,
        waveform_b64: str = Config.WAVEFORM_PLACEHOLDER,
        image_data: Optional[bytes] = None,
        image_filename: Optional[str] = None,
        existing_bot_messages_to_edit: Optional[TypingList[discord.Message]] = None
    ) -> TypingList[discord.Message]:
        """
        Sends a reply. Can handle text, image, and audio content.
        Returns a list of all messages sent.
        """
        all_sent_messages: TypingList[discord.Message] = []
        if existing_bot_messages_to_edit:
            can_safely_edit = (
                len(existing_bot_messages_to_edit) == 1 and
                text_content and not audio_data and not image_data and
                not existing_bot_messages_to_edit[0].attachments and
                not (existing_bot_messages_to_edit[0].flags and existing_bot_messages_to_edit[0].flags.voice) and
                len(text_content) <= Config.MAX_MESSAGE_LENGTH
            )
            if can_safely_edit:
                try:
                    edited_message = await existing_bot_messages_to_edit[0].edit(content=text_content)
                    logger.info(f"✏️ Edited existing bot message with text. ID: {edited_message.id}")
                    try: await edited_message.add_reaction(Config.RETRY_EMOJI)
                    except discord.HTTPException as e_react: logger.warning(f"⚠️ Could not add retry reaction to edited message {edited_message.id}: {e_react}")
                    return [edited_message]
                except discord.HTTPException as e:
                    logger.warning(f"⚠️ Failed to edit text-only bot message (ID: {existing_bot_messages_to_edit[0].id}). Error: {e}. Will delete and resend.", exc_info=False)
            for msg_to_delete in existing_bot_messages_to_edit:
                try:
                    await msg_to_delete.delete()
                    logger.info(f"🗑️ Deleted old bot message (ID: {msg_to_delete.id}) to allow resending.")
                except discord.HTTPException as e_del:
                    logger.warning(f"⚠️ Could not delete old bot message (ID: {msg_to_delete.id}) for resend. Error: {e_del}", exc_info=False)
        discord_file_to_send = None
        temp_image_path = None
        try:
            if image_data:
                filename_for_discord = image_filename if image_filename else "plot.png"
                _, suffix = os.path.splitext(filename_for_discord)
                if not suffix:
                    suffix = '.png'
                with tempfile.NamedTemporaryFile(suffix=suffix, delete=False) as temp_image_file:
                    temp_image_file.write(image_data)
                    temp_image_path = temp_image_file.name
                discord_file_to_send = discord.File(temp_image_path, filename=filename_for_discord)
                logger.info(f"📎 Prepared {filename_for_discord} for sending.")
            if text_content or discord_file_to_send:
                text_and_image_messages = await MessageSender._send_text_reply(message_to_reply_to, text_content, discord_file_to_send)
                if text_and_image_messages:
                    all_sent_messages.extend(text_and_image_messages)
                else:
                    logger.error("❌ Failed to send text content or image. Audio sending will still be attempted if audio data is present.")
        finally:
            if temp_image_path and os.path.exists(temp_image_path):
                try:
                    os.unlink(temp_image_path)
                except OSError as e:
                    logger.warning(f"⚠️ Could not delete temporary image file {temp_image_path}: {e}")
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
                    logger.info(f"REQUEST to Discord API (get_upload_slot):\nURL: {upload_slot_api_url}\n"
                                f"Headers: {upload_slot_headers}\n"
                                f"Payload:\n{json.dumps(upload_slot_payload, indent=2)}")
                    async with session.post(upload_slot_api_url, json=upload_slot_payload, headers=upload_slot_headers) as resp_slot:
                        if resp_slot.status == 200:
                            resp_slot_json = await resp_slot.json()
                            logger.info(f"RESPONSE from Discord API (get_upload_slot):\nStatus: {resp_slot.status}\n"
                                        f"JSON:\n{json.dumps(resp_slot_json, indent=2)}")
                            if resp_slot_json.get("attachments") and len(resp_slot_json["attachments"]) > 0:
                                attachment_metadata = resp_slot_json["attachments"][0]
                            else:
                                raise Exception("Invalid attachment slot response from Discord API.")
                        else:
                            response_text = await resp_slot.text()
                            logger.error(f"RESPONSE from Discord API (get_upload_slot) was not successful:\nStatus: {resp_slot.status}\n"
                                         f"Body:\n{response_text}")
                            raise Exception("Failed to get Discord upload slot.")
                    put_url = attachment_metadata["upload_url"]
                    put_headers = {'Content-Type': 'audio/ogg'}
                    logger.info(f"REQUEST to Discord CDN (put_audio):\nURL: {put_url}\n"
                                f"Headers: {put_headers}\n"
                                f"Body: Raw OGG audio data (size: {len(audio_data)} bytes)")
                    with open(temp_ogg_file_path_for_upload, 'rb') as file_to_put:
                        async with session.put(put_url, data=file_to_put, headers=put_headers) as resp_put:
                            if resp_put.status != 200:
                                response_text = await resp_put.text()
                                logger.error(f"RESPONSE from Discord CDN (put_audio) was not successful:\nStatus: {resp_put.status}\n"
                                             f"Body:\n{response_text}")
                                raise Exception("Failed to PUT audio to Discord CDN.")
                            else:
                                logger.info(f"RESPONSE from Discord CDN (put_audio):\nStatus: {resp_put.status}")
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
                    logger.info(f"REQUEST to Discord API (send_voice_message):\nURL: {send_message_api_url}\n"
                                f"Headers: {send_message_headers}\n"
                                f"Payload:\n{json.dumps(send_message_payload, indent=2)}")
                    async with session.post(send_message_api_url, json=send_message_payload, headers=send_message_headers) as resp_send:
                        response_data = await resp_send.json()
                        logger.info(f"RESPONSE from Discord API (send_voice_message):\nStatus: {resp_send.status}\n"
                                    f"JSON:\n{json.dumps(response_data, indent=2)}")
                        if resp_send.status == 200 or resp_send.status == 201:
                            message_id = response_data.get("id")
                            if message_id:
                                try:
                                    fetched_msg = await message_to_reply_to.channel.fetch_message(message_id)
                                    sent_native_voice_message_obj = fetched_msg
                                except discord.HTTPException as e_fetch:
                                    logger.warning(f"🎤 Sent native voice message (ID: {message_id}), but failed to fetch Message object. Error: {e_fetch}")
                            else:
                                raise Exception("Discord API reported success for voice message but returned no message ID.")
                        else:
                            raise Exception("Discord API send voice message failed.")
            except Exception as e:
                logger.error(f"❌ Error sending native Discord voice message. Will attempt fallback if applicable.\nError:\n{e}", exc_info=True)
            finally:
                if temp_ogg_file_path_for_upload and os.path.exists(temp_ogg_file_path_for_upload):
                    try: os.unlink(temp_ogg_file_path_for_upload)
                    except OSError: pass
            if sent_native_voice_message_obj:
                try: await sent_native_voice_message_obj.add_reaction(Config.RETRY_EMOJI)
                except discord.HTTPException as e_react: logger.warning(f"⚠️ Could not add retry reaction to voice message {sent_native_voice_message_obj.id}: {e_react}")
                all_sent_messages.append(sent_native_voice_message_obj)
            else:
                logger.info("🎤 Native voice send unsuccessful or unconfirmed, attempting to send audio as file attachment.")
                temp_ogg_path_regular = None
                sent_audio_file_message_fallback: Optional[discord.Message] = None
                try:
                    with tempfile.NamedTemporaryFile(suffix='.ogg', delete=False) as temp_audio_file_fallback:
                        temp_audio_file_fallback.write(audio_data)
                        temp_ogg_path_regular = temp_audio_file_fallback.name
                    discord_file = discord.File(temp_ogg_path_regular, "voice_response.ogg")
                    if all_sent_messages:
                        sent_audio_file_message_fallback = await message_to_reply_to.channel.send(file=discord_file)
                    else:
                        sent_audio_file_message_fallback = await message_to_reply_to.reply(file=discord_file)
                    if sent_audio_file_message_fallback:
                        logger.info(f"📎 Sent voice response as .ogg file attachment (fallback). ID: {sent_audio_file_message_fallback.id}")
                        try: await sent_audio_file_message_fallback.add_reaction(Config.RETRY_EMOJI)
                        except discord.HTTPException as e_react: logger.warning(f"⚠️ Could not add retry reaction to fallback audio message {sent_audio_file_message_fallback.id}: {e_react}")
                        all_sent_messages.append(sent_audio_file_message_fallback)
                except discord.HTTPException as e_file:
                    logger.error(f"❌ Failed to send .ogg file as attachment (fallback). Error: {e_file}", exc_info=True)
                except Exception as e_gen_file:
                    logger.error(f"❌ Unexpected error sending .ogg file (fallback). Error: {e_gen_file}", exc_info=True)
                finally:
                    if temp_ogg_path_regular and os.path.exists(temp_ogg_path_regular):
                        try: os.unlink(temp_ogg_path_regular)
                        except OSError: pass
        if not all_sent_messages and (text_content or audio_data or image_data):
            logger.error("❌ All attempts to send content (text, image, or audio) failed.")
        return all_sent_messages
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