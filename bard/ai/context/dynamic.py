from bard.bot.types import DiscordContext


class DynamicContextFormatter:
    """
    Encapsulates methods for formatting dynamic context elements.
    """

    @staticmethod
    def format_discord_context(context: DiscordContext) -> str:
        """
        Formats the Discord context dictionary into a readable string for the prompt.

        Args:
            context: A dictionary containing Discord-specific context information.

        Returns:
            A formatted string representing the Discord context.
        """
        formatted_context = [
            "[DYNAMIC_CONTEXT]",
            f"Channel ID: <#{context['channel_id']}>",
            f"Channel Name: {context['channel_name']}",
        ]
        if context["channel_topic"]:
            formatted_context.append(f"Channel Topic: {context['channel_topic']}")

        if context["users_in_channel"]:
            users_formatted = " ".join(
                [f"<@{user_id}>" for user_id in context["users_in_channel"]]
            )
            formatted_context.append(f"Users in Channel: {users_formatted}")

        formatted_context.append(f"Sender User ID: <@{context['sender_user_id']}>")
        formatted_context.append(f"Replied User ID: <@{context['replied_user_id']}>")

        formatted_context.append(f"Current Time (UTC): {context['current_time_utc']}")
        formatted_context.append("[/DYNAMIC_CONTEXT]")
        return "\n".join(formatted_context)
