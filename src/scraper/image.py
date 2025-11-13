import asyncio
import base64
import logging
from typing import Optional
from urllib.parse import quote_plus

import aiohttp
from playwright.async_api import Error

from scraper.scraper import Scraper
from settings import Settings

log = logging.getLogger("Bard")


class ImageScraper:
    """
    A scraper specifically designed to find and retrieve a single image from a given URL.
    """

    def __init__(self, scraper: Scraper):
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
        page = await self.scraper._browser.new_page()

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

            log.debug("Searching for thumbnail to click.")
            images = await page.query_selector_all("img")
            thumbnail_to_click = None
            for img in images:
                src = await img.get_attribute("src")
                if src and src.startswith("data:image"):
                    box = await img.bounding_box()
                    if box and box["width"] > 50 and box["height"] > 50:
                        thumbnail_to_click = img
                        log.debug("Found thumbnail to click.")
                        break

            if not thumbnail_to_click:
                log.warning("No suitable data URI thumbnails found.")
                return None

            await thumbnail_to_click.click()
            await asyncio.sleep(1)

            preview_img_selector = "img.sFlh5c"
            log.debug(f"Waiting for preview image selector: {preview_img_selector}")

            try:
                image_element = await page.wait_for_selector(
                    preview_img_selector, timeout=10000
                )
            except Error as e:
                log.error(f"Timeout waiting for preview image: {e}")
                await page.screenshot(path="debug_screenshot_preview_timeout.png")
                return None

            if not image_element:
                log.warning(
                    "Could not find the main image element in the preview pane."
                )
                return None

            best_image_url = await image_element.get_attribute("src")

            if not best_image_url:
                log.warning("Could not extract image source from the preview pane.")
                return None

            log.debug(
                "Found image URL, attempting to fetch data.",
                extra={"url": best_image_url[:100] + "..."},
            )

            if best_image_url.startswith("data:image"):
                header, encoded = best_image_url.split(",", 1)
                image_data = base64.b64decode(encoded)
                log.info(
                    "Successfully extracted image data from data URI.",
                    extra={"search_terms": search_terms},
                )
                return image_data
            else:
                headers = {
                    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/119.0.0.0 Safari/537.36"
                }
                async with aiohttp.ClientSession(headers=headers) as session:
                    async with session.get(best_image_url) as resp:
                        if resp.status == 200:
                            image_data = await resp.read()
                            log.info(
                                "Successfully downloaded image data.",
                                extra={
                                    "search_terms": search_terms,
                                    "url": best_image_url,
                                },
                            )
                            return image_data
                        else:
                            log.warning(
                                "Failed to download image.",
                                extra={
                                    "url": best_image_url,
                                    "status_code": resp.status,
                                },
                            )
                            return None

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
