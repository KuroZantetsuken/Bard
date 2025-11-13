from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional


@dataclass
class VideoDetails:
    """
    A data structure to hold details about a processed URL, indicating
    whether it is a video and containing relevant metadata.
    """

    is_video: bool = False
    is_youtube: bool = False
    metadata: Optional[dict[str, Any]] = None
    video_path: Optional[str] = None


@dataclass
class ResolvedURL:
    """Represents a URL that has been resolved to its final destination."""

    original: str
    resolved: str


@dataclass
class ScrapedMedia:
    """Represents a single media item (image or video) found on a page."""

    media_type: str
    url: str
    metadata: Dict[str, Any] = field(default_factory=dict)


@dataclass
class ScrapedData:
    """
    Represents all structured data extracted from a single URL,
    designed to be grouped for AI context.
    """

    url: ResolvedURL
    title: Optional[str]
    text_content: str
    screenshot_data: Optional[bytes]
    timestamp: float
    media: List[ScrapedMedia] = field(default_factory=list)
    video_details: Optional[VideoDetails] = None
    metadata: Dict[str, Any] = field(default_factory=dict)


@dataclass
class CachedObject:
    """The object stored in the filesystem cache."""

    data: ScrapedData
    expires: float
