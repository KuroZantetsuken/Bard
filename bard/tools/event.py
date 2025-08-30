import logging
from datetime import datetime
from typing import Any, Dict, List

import aiohttp
import discord
from google.genai.types import FunctionDeclaration, FunctionResponse, Schema, Type

from bard.tools.base import BaseTool, ToolContext

logger = logging.getLogger("Bard")


class DiscordEventTool(BaseTool):
    tool_emoji = "📅"

    def __init__(self, context: ToolContext):
        super().__init__(context=context)

    def get_function_declarations(self) -> List[FunctionDeclaration]:
        """
        Declares the functions provided by the Discord event tool.
        """
        return [
            FunctionDeclaration(
                name="create_discord_event",
                description="Purpose: This tool creates a new scheduled event in the Discord server. Arguments: Fill out as many arguments as you can using the user's message, supplementing it with information gathered using other tools first. Results: Upon success, will create a Discord event, with no specific further tasks from the AI other than acknowledging this appropriately. Restrictions/Guidelines: Only use this tool if event creation is requested. If the user's request is about a known topic (e.g., a game release, movie premiere), use other tools first to find the specific details like the official date, time, description, and a relevant cover image URL. If no location is specified, the AI should use context clues to put something useful and relevant, such as a website URL, or default to the channel where the request was made.",
                parameters=Schema(
                    type=Type.OBJECT,
                    properties={
                        "name": Schema(
                            type=Type.STRING,
                            description="The name of the event. MAX 100 CHARACTERS.",
                        ),
                        "description": Schema(
                            type=Type.STRING,
                            description="A detailed description for the event. The AI can generate this if not provided. MAX 1000 CHARACTERS.",
                        ),
                        "start_time": Schema(
                            type=Type.STRING,
                            description='The scheduled start time in ISO 8601 format (e.g., "2025-09-01T17:00:00Z").',
                        ),
                        "end_time": Schema(
                            type=Type.STRING,
                            description="The scheduled end time in ISO 8601 format.",
                        ),
                        "location": Schema(
                            type=Type.STRING,
                            description="The location of the event (e.g., most likely a website URL, or default to the channel where the request was made).",
                        ),
                        "image_url": Schema(
                            type=Type.STRING,
                            description="A direct URL for the event's cover image (e.g., ending in .png, .jpg, .gif). The AI should use the InternetTool to find a suitable direct image URL.",
                        ),
                    },
                    required=["name", "start_time"],
                ),
            ),
            FunctionDeclaration(
                name="delete_discord_event",
                description="Deletes an existing scheduled event from the Discord server. This action is permanent. The 'get_discord_events' tool should be used first to obtain a list of events and their IDs for precise deletion. If only a name is provided and multiple events share a similar name, the AI should ask for clarification before proceeding.",
                parameters=Schema(
                    type=Type.OBJECT,
                    properties={
                        "name": Schema(
                            type=Type.STRING,
                            description="The name of the event to be deleted. Used if ID is not provided.",
                        ),
                        "id": Schema(
                            type=Type.STRING,
                            description="The unique ID of the event to be deleted. Prefer using ID over name for precision.",
                        ),
                    },
                    required=[],
                ),
            ),
            FunctionDeclaration(
                name="get_discord_events",
                description="Retrieves a list of scheduled events from the Discord server. This tool can be used to get information about active events, which can then be used for other operations like deleting events.",
                parameters=Schema(
                    type=Type.OBJECT,
                    properties={},
                    required=[],
                ),
            ),
        ]

    async def _create_event(
        self, args: Dict[str, Any], context: ToolContext
    ) -> FunctionResponse:
        """
        Creates a new scheduled event in the Discord server.
        """
        logger.debug("Entered _create_event")
        guild = context.get("guild")
        if not guild:
            return self.function_response_error(
                "create_discord_event", "Discord guild not found in context."
            )

        name = args.get("name")
        description = args.get("description")
        start_time_str = args.get("start_time")
        end_time_str = args.get("end_time")
        location = args.get("location")
        image_url = args.get("image_url")

        if not start_time_str:
            return self.function_response_error(
                "create_discord_event", "start_time is required."
            )

        if not location:
            channel = context.get("channel")
            if channel and isinstance(channel, discord.TextChannel):
                location = channel.mention
            else:
                location = "Online"

        try:
            start_time = datetime.fromisoformat(start_time_str)
            end_time = datetime.fromisoformat(end_time_str) if end_time_str else None
        except ValueError as e:
            return self.function_response_error(
                "create_discord_event", f"Invalid ISO 8601 date format: {e}"
            )

        if description and len(description) > 1000:
            description = description[:1000]
            logger.warning("Event description truncated to 1000 characters.")

        logger.debug("Checking for duplicate events...")
        for event in guild.scheduled_events:
            if (
                event.name == name
                and event.start_time.astimezone() == start_time.astimezone()
            ):
                logger.info(f"Event '{name}' already exists. Skipping creation.")
                return self.function_response_success(
                    "create_discord_event",
                    f"Event '{name}' already exists with the same start time.",
                    id=str(event.id),
                    name=event.name,
                )

        image_bytes = None
        if image_url:
            try:
                async with aiohttp.ClientSession() as session:
                    async with session.get(image_url) as resp:
                        if resp.status == 200:
                            image_bytes = await resp.read()
                        else:
                            logger.warning(
                                f"Failed to fetch image from {image_url}: HTTP status {resp.status}. Proceeding without image."
                            )
            except aiohttp.ClientError as e:
                logger.error(f"Error fetching image: {e}. Proceeding without image.")

        try:
            event_params = {
                "name": name,
                "description": description,
                "start_time": start_time,
                "entity_type": discord.EntityType.external,
                "location": location,
                "privacy_level": discord.PrivacyLevel.guild_only,
            }
            if end_time:
                event_params["end_time"] = end_time
            if image_bytes:
                event_params["image"] = image_bytes

            logger.debug(
                f"Attempting to create event with params: {name}, {description}, {start_time}, {end_time}, {location}"
            )

            event = await guild.create_scheduled_event(**event_params)

            logger.debug(
                f"Successfully created event '{event.name}' (ID: {event.id}) on Discord."
            )

            event_url = f"https://discord.com/events/{guild.id}/{event.id}"

            response = self.function_response_success(
                "create_discord_event",
                f"Event '{event.name}' (ID: {event.id}) created successfully. Link: {event_url}",
                id=str(event.id),
                name=event.name,
                url=event_url,
            )
            logger.debug(f"create_discord_event response: {response.model_dump()}")
            return response
        except Exception as e:
            logger.exception(f"Failed to create event: {e}")
            response = self.function_response_error(
                "create_discord_event", f"An unexpected error occurred: {e}"
            )
            logger.debug(
                f"create_discord_event error response: {response.model_dump()}"
            )
            return response

    async def _delete_event(
        self, args: Dict[str, Any], context: ToolContext
    ) -> FunctionResponse:
        """
        Deletes a scheduled event from the Discord server by ID or name.
        """
        guild = context.get("guild")
        if not guild:
            return self.function_response_error(
                "delete_discord_event", "Discord guild not found in context."
            )

        event_id = args.get("id")
        event_name = args.get("name")

        if not event_id and not event_name:
            return self.function_response_error(
                "delete_discord_event",
                "Either event ID or name is required for deletion.",
            )

        target_event = None
        if event_id:
            for event in guild.scheduled_events:
                if str(event.id) == event_id:
                    target_event = event
                    break
        elif event_name:
            matching_events = [
                event for event in guild.scheduled_events if event.name == event_name
            ]
            if len(matching_events) == 1:
                target_event = matching_events[0]
            elif len(matching_events) > 1:
                return self.function_response_error(
                    "delete_discord_event",
                    f"Multiple events found with the name '{event_name}'. Please provide a unique ID or a more specific name.",
                )

        if not target_event:
            return self.function_response_error(
                "delete_discord_event", f"Event '{event_name or event_id}' not found."
            )

        try:
            await target_event.delete()
            return self.function_response_success(
                "delete_discord_event",
                f"Event '{target_event.name}' (ID: {target_event.id}) deleted successfully.",
            )
        except Exception as e:
            logger.exception(
                f"Failed to delete event '{target_event.name}' (ID: {target_event.id}): {e}"
            )
            return self.function_response_error(
                "delete_discord_event",
                f"Failed to delete event '{target_event.name}' (ID: {target_event.id}): {e}",
            )

    async def _get_events(
        self, args: Dict[str, Any], context: ToolContext
    ) -> FunctionResponse:
        """
        Retrieves a list of scheduled events from the Discord server.
        """
        guild = context.get("guild")
        if not guild:
            return self.function_response_error(
                "get_discord_events", "Discord guild not found in context."
            )

        events_data = []
        for event in guild.scheduled_events:
            events_data.append(
                {
                    "id": str(event.id),
                    "name": event.name,
                    "description": event.description,
                    "start_time": event.start_time.isoformat()
                    if event.start_time
                    else None,
                    "end_time": event.end_time.isoformat() if event.end_time else None,
                    "location": event.location,
                    "status": str(event.status),
                    "url": f"https://discord.com/events/{guild.id}/{event.id}",
                }
            )

        if not events_data:
            return self.function_response_success(
                "get_discord_events", "No scheduled events found.", events=[]
            )

        return self.function_response_success(
            "get_discord_events",
            "Successfully retrieved scheduled events.",
            events=events_data,
        )

    async def execute_tool(
        self, function_name: str, args: Dict[str, Any], context: ToolContext
    ) -> FunctionResponse:
        """
        Executes a function based on the provided function name and arguments.
        """
        try:
            if function_name == "create_discord_event":
                return await self._create_event(args, context)
            elif function_name == "delete_discord_event":
                return await self._delete_event(args, context)
            elif function_name == "get_discord_events":
                return await self._get_events(args, context)
            else:
                return self.function_response_error(
                    function_name, f"Unknown function: {function_name}"
                )
        except Exception as e:
            logger.exception(f"Error executing tool '{function_name}': {e}")
            return self.function_response_error(
                function_name, f"An unexpected error occurred: {e}"
            )
