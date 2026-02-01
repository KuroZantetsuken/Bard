import base64
import logging
from typing import List, Optional
from urllib.parse import quote_plus

import aiohttp
from playwright.async_api import ElementHandle, Error, Page

from scraper.scraper import Scraper
from settings import Settings

log = logging.getLogger("Bard")


class ImageScraper:
    """
    A robust scraper designed to find and retrieve a single image from Google Images.
    Uses multiple heuristic strategies to avoid breakage from CSS selector changes.
    """

    # Heuristic constants
    MIN_THUMBNAIL_SIZE = 50
    MIN_PREVIEW_SIZE = 200
    PREVIEW_WAIT_TIMEOUT_MS = 10000

    # Fallback selectors that have historically worked or follow semantic patterns
    # We avoid obfuscated classes here unless they are common or necessary fallbacks
    PREVIEW_SELECTORS: List[str] = [
        "img[jsname='JuXqh']",  # Common Google Images preview jsname
        "img.sFlh5c",  # Current known obfuscated class
        "a[role='link'] img",  # Images inside result links
        "div[role='region'] img",
        "div[role='dialog'] img",
        "div.hh1Ztf img",  # Known container class
    ]

    def __init__(self, scraper: Scraper) -> None:
        """
        Initializes the ImageScraper.

        Args:
            scraper: An instance of the main Scraper class to reuse its browser context.
        """
        self.scraper = scraper

    async def scrape_image_data(self, search_terms: str) -> Optional[bytes]:
        """
        Scrapes a single image from a Google Images search and returns its data.

        Args:
            search_terms: The terms to search for on Google Images.

        Returns:
            The image data as bytes, or None if an image could not be found or fetched.
        """
        if not self.scraper._browser:
            await self.scraper.launch_browser()

        assert self.scraper._browser is not None
        page: Page = await self.scraper._browser.new_page()

        try:
            log.info(
                "Scraping image for search terms.",
                extra={"search_terms": search_terms},
            )
            encoded_search_terms = quote_plus(search_terms)
            url = f"https://www.google.com/search?q={encoded_search_terms}&tbm=isch"

            await page.goto(
                url,
                wait_until="networkidle",
                timeout=Settings.TOOL_TIMEOUT_SECONDS * 1000,
            )

            thumbnail = await self._find_thumbnail(page)
            if not thumbnail:
                log.warning(
                    "No suitable thumbnails found for search terms.",
                    extra={"search_terms": search_terms},
                )
                return None

            await thumbnail.click()

            # Wait for the preview pane to open and load the image
            best_image_url = await self._extract_preview_url(page)

            if not best_image_url:
                log.warning(
                    "Could not extract preview image URL.",
                    extra={"search_terms": search_terms},
                )
                return None

            log.debug(
                "Found image URL, attempting to fetch data.",
                extra={"url": best_image_url[:100] + "..."},
            )

            return await self._fetch_image_data(best_image_url, search_terms)

        except Error as e:
            log.error(
                "Playwright error while scraping image.",
                extra={"search_terms": search_terms, "error": str(e)},
            )
            return None
        except Exception as e:
            log.error(
                "An unexpected error occurred during image scraping.",
                extra={"search_terms": search_terms, "error": str(e)},
                exc_info=True,
            )
            return None
        finally:
            await page.close()

    async def _find_thumbnail(self, page: Page) -> Optional[ElementHandle]:
        """
        Heuristically finds a suitable thumbnail to click.
        """
        log.debug("Searching for thumbnail to click.")

        # Try finding images that look like search results
        images = await page.query_selector_all("img")
        for img in images:
            try:
                src = await img.get_attribute("src")
                if not src:
                    continue

                # Thumbnails are usually data URIs or have specific patterns
                if src.startswith("data:image") or "encrypted-tbn" in src:
                    box = await img.bounding_box()
                    if (
                        box
                        and box["width"] >= self.MIN_THUMBNAIL_SIZE
                        and box["height"] >= self.MIN_THUMBNAIL_SIZE
                    ):
                        # Avoid clicking UI icons/logos which might be small but captured
                        if (
                            box["width"] < 300
                        ):  # Thumbnails aren't usually huge initially
                            return img
            except Error:
                continue

        return None

    async def _extract_preview_url(self, page: Page) -> Optional[str]:
        """
        Heuristically extracts the high-quality image URL from the preview pane.
        Tries multiple strategies including fallback selectors and size-based heuristics.
        """
        # Strategy 1: Fallback selectors
        for selector in self.PREVIEW_SELECTORS:
            try:
                log.debug(f"Trying preview selector: {selector}")
                # Wait briefly for each selector
                element = await page.wait_for_selector(selector, timeout=2000)
                if element:
                    src = await element.get_attribute("src")
                    if src and self._is_valid_image_url(src):
                        # Verify it's actually the preview (should be visible and large)
                        box = await element.bounding_box()
                        if box and box["width"] >= self.MIN_PREVIEW_SIZE:
                            log.debug(
                                f"Found valid preview image with selector: {selector}"
                            )
                            return src
            except Error:
                continue

        # Strategy 2: Largest visible image that isn't a known small thumbnail
        log.debug("Falling back to largest visible image heuristic.")
        try:
            # We use a slightly more complex script to find the best candidate
            best_src = await page.evaluate(f"""() => {{
                const imgs = Array.from(document.querySelectorAll('img'));
                const candidates = imgs.map(img => {{
                    const rect = img.getBoundingClientRect();
                    return {{
                        src: img.src,
                        width: rect.width,
                        height: rect.height,
                        area: rect.width * rect.height
                    }};
                }}).filter(img => 
                    img.width >= {self.MIN_PREVIEW_SIZE} && 
                    img.height >= {self.MIN_PREVIEW_SIZE} && 
                    img.src && 
                    !img.src.includes('placeholder') &&
                    !img.src.startsWith('data:image/gif')
                );
                
                if (candidates.length === 0) return null;
                
                // Sort by area descending
                candidates.sort((a, b) => b.area - a.area);
                return candidates[0].src;
            }}""")
            return str(best_src) if best_src else None
        except Error as e:
            log.error(f"Error in largest image heuristic: {e}")
            return None

    def _is_valid_image_url(self, url: str) -> bool:
        """Checks if a URL looks like a valid image source."""
        return bool(
            url
            and not url.startswith("data:image/gif")
            and (url.startswith("http") or url.startswith("data:image"))
        )

    async def _fetch_image_data(self, url: str, search_terms: str) -> Optional[bytes]:
        """
        Fetches image bytes from a URL (handles data URIs and HTTP URLs).
        """
        if url.startswith("data:image"):
            try:
                if "," not in url:
                    log.error("Invalid data URI format.")
                    return None
                header, encoded = url.split(",", 1)
                image_data = base64.b64decode(encoded)
                log.info(
                    "Successfully extracted image data from data URI.",
                    extra={"search_terms": search_terms},
                )
                return image_data
            except Exception as e:
                log.error(f"Failed to decode data URI: {e}")
                return None
        else:
            headers = {
                "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/119.0.0.0 Safari/537.36"
            }
            try:
                timeout = aiohttp.ClientTimeout(total=10)
                async with aiohttp.ClientSession(
                    headers=headers, timeout=timeout
                ) as session:
                    async with session.get(url) as resp:
                        if resp.status == 200:
                            image_data = await resp.read()
                            log.info(
                                "Successfully downloaded image data.",
                                extra={
                                    "search_terms": search_terms,
                                    "url": url,
                                },
                            )
                            return image_data
                        else:
                            log.warning(
                                "Failed to download image.",
                                extra={
                                    "url": url,
                                    "status_code": resp.status,
                                },
                            )
                            return None
            except Exception as e:
                log.error(f"Error downloading image from {url}: {e}")
                return None
