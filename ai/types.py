from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional


@dataclass
class FinalAIResponse:
    """
    A structured representation of the AI's final response data.
    This dataclass encapsulates the text content, any associated media files,
    emojis representing used tools, and the original Discord message ID.
    """

    text_content: Optional[str] = None
    media: Dict[str, Any] = field(default_factory=dict)
    tool_emojis: List[str] = field(default_factory=list)
    message_id: Optional[int] = None
