import logging
from datetime import datetime
from typing import Any, Dict, List

import aiohttp
import discord
from google.genai.types import FunctionDeclaration, FunctionResponse, Schema, Type

from tools.base import BaseTool, ToolContext

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
                description="Purpose: This tool creates a new scheduled event in the Discord server. Arguments: Fill out as many arguments as you can using the user's message, supplementing it with information gathered using other tools first. Results: Upon success, will create a Discord event, with no specific further tasks from the AI other than acknowledging this appropriately. Restrictions/Guidelines: Only use this tool if event creation is requested. If the user's request is about a known topic (e.g., a game release, movie premiere), use other tools first to find the specific details like the official date, time, description, and a relevant cover image URL. If no location is specified, use context clues to put something useful and relevant, such as a website URL.",
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
                    required=["name", "start_time", "end_time", "location"],
                ),
            ),
            FunctionDeclaration(
                name="delete_discord_event",
                description="Deletes an existing scheduled event from the Discord server by its exact name. This action is permanent. If multiple events share a similar name, the AI should ask for clarification before proceeding.",
                parameters=Schema(
                    type=Type.OBJECT,
                    properties={
                        "name": Schema(
                            type=Type.STRING,
                            description="The name of the event to be deleted.",
                        ),
                    },
                    required=["name"],
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
        if not end_time_str:
            return self.function_response_error(
                "create_discord_event", "end_time is required for external events."
            )

        try:
            start_time = datetime.fromisoformat(start_time_str)
            end_time = datetime.fromisoformat(end_time_str)
        except ValueError as e:
            return self.function_response_error(
                "create_discord_event", f"Invalid ISO 8601 date format: {e}"
            )

        # Truncate description to 1000 characters if it exceeds the limit
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
                "end_time": end_time,
                "entity_type": discord.EntityType.external,
                "location": location,
                "privacy_level": discord.PrivacyLevel.guild_only,
            }
            if image_bytes:
                event_params["image"] = image_bytes

            logger.debug(
                f"Attempting to create event with params: {name}, {description}, {start_time}, {end_time}, {location}"
            )

            event = await guild.create_scheduled_event(**event_params)

            logger.debug(
                f"Successfully created event '{event.name}' (ID: {event.id}) on Discord."
            )

            # Construct the event URL
            event_url = f"https://discord.com/events/{guild.id}/{event.id}"

            response = self.function_response_success(
                "create_discord_event",
                f"Event '{event.name}' (ID: {event.id}) created successfully. Link: {event_url}",
                id=str(event.id),
                name=event.name,
                url=event_url,  # Include the URL in the response data
            )
            logger.debug(f"create_discord_event response: {response.model_dump()}")
            return response
        except Exception as e:
            logger.exception(f"Failed to create event: {e}")
            response = self.function_response_error(
                "create_discord_event", f"Failed to create event: {e}"
            )
            logger.debug(
                f"create_discord_event error response: {response.model_dump()}"
            )
            return response

    async def _delete_event(
        self, args: Dict[str, Any], context: ToolContext
    ) -> FunctionResponse:
        """
        Deletes a scheduled event from the Discord server by name.
        """
        guild = context.get("guild")
        if not guild:
            return self.function_response_error(
                "delete_discord_event", "Discord guild not found in context."
            )

        event_name = args.get("name")
        if not event_name:
            return self.function_response_error(
                "delete_discord_event", "Event name is required for deletion."
            )

        for event in guild.scheduled_events:
            if event.name == event_name:
                try:
                    await event.delete()
                    return self.function_response_success(
                        "delete_discord_event",
                        f"Event '{event_name}' deleted successfully.",
                    )
                except Exception as e:
                    logger.exception(f"Failed to delete event '{event_name}': {e}")
                    return self.function_response_error(
                        "delete_discord_event",
                        f"Failed to delete event '{event_name}': {e}",
                    )

        return self.function_response_error(
            "delete_discord_event", f"Event '{event_name}' not found."
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
            else:
                return self.function_response_error(
                    function_name, f"Unknown function: {function_name}"
                )
        except Exception as e:
            logger.exception(f"Error executing tool '{function_name}': {e}")
            return self.function_response_error(
                function_name, f"An unexpected error occurred: {e}"
            )
