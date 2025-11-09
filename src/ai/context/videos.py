from bot.types import VideoMetadata


class VideoFormatter:
    """
    Encapsulates methods for formatting video related context elements.
    """

    def format_video_metadata(self, metadata: VideoMetadata) -> str:
        """
        Formats video metadata into a readable string for the prompt.

        Args:
            metadata: A VideoMetadata object containing details about a video.

        Returns:
            A formatted string representing the video metadata.
        """
        formatted_metadata = ["[VIDEOS:START]"]
        if metadata.title:
            formatted_metadata.append(f"Title: {metadata.title}")
        if metadata.description:
            formatted_metadata.append(f"Description: {metadata.description}")
        if metadata.duration_seconds is not None:
            formatted_metadata.append(f"Duration: {metadata.duration_seconds} seconds")
        if metadata.upload_date:
            formatted_metadata.append(f"Upload Date: {metadata.upload_date}")
        if metadata.uploader:
            formatted_metadata.append(f"Uploader: {metadata.uploader}")
        if metadata.view_count is not None:
            formatted_metadata.append(f"View Count: {metadata.view_count}")
        if metadata.average_rating is not None:
            formatted_metadata.append(f"Average Rating: {metadata.average_rating}")
        if metadata.categories:
            formatted_metadata.append(f"Categories: {', '.join(metadata.categories)}")
        if metadata.tags:
            formatted_metadata.append(f"Tags: {', '.join(metadata.tags)}")
        formatted_metadata.append(f"Is YouTube: {metadata.is_youtube}")
        formatted_metadata.append(f"URL: {metadata.url}")
        formatted_metadata.append("[VIDEOS:END]")
        return "\n".join(formatted_metadata)
