from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

from bard.scraping.video import VideoDetails


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

    resolved_url: str
    title: Optional[str]
    text_content: str
    screenshot_data: Optional[bytes]  # Raw bytes of the PNG screenshot
    timestamp: float
    media: List[ScrapedMedia] = field(default_factory=list)
    video_details: Optional[VideoDetails] = None
    metadata: Dict[str, Any] = field(default_factory=dict)


@dataclass
class CachedObject:
    """The object stored in the filesystem cache."""

    data: ScrapedData
    expires: float
