import logging
from typing import Optional
from urllib.parse import urljoin

from bs4 import BeautifulSoup, Tag
from bs4.element import PageElement

logger = logging.getLogger("Bard")


async def extract_image_url_from_html(
    html_content: str, base_url: str
) -> Optional[str]:
    """
    Extracts an image URL from HTML content. Prioritizes Open Graph/Twitter Card images,
    then looks for the first <img> tag.

    Args:
        html_content: The HTML content as a string.
        base_url: The base URL of the HTML page, used for resolving relative URLs.

    Returns:
        An optional string containing the extracted absolute image URL, or None if no suitable image is found.
    """
    soup = BeautifulSoup(html_content, "html.parser")

    og_image_element: Optional[PageElement] = soup.find("meta", property="og:image")
    if og_image_element and isinstance(og_image_element, Tag):
        og_content = og_image_element.get("content")
        if og_content:
            return urljoin(base_url, str(og_content))

    twitter_image_element: Optional[PageElement] = soup.find(
        "meta", attrs={"name": "twitter:image"}
    )
    if twitter_image_element and isinstance(twitter_image_element, Tag):
        twitter_content = twitter_image_element.get("content")
        if twitter_content:
            return urljoin(base_url, str(twitter_content))

    img_element: Optional[PageElement] = soup.find("img")
    if img_element and isinstance(img_element, Tag):
        img_src = img_element.get("src")
        if img_src:
            return urljoin(base_url, str(img_src))

    return None
