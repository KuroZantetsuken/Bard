import asyncio
import logging
import os
import time
from typing import Optional

from bs4 import BeautifulSoup
from playwright.async_api import Error, async_playwright

from bard.scraping.extensions import ExtensionManager
from bard.scraping.models import ScrapedData
from config import Config

logger = logging.getLogger("Bard")


class Scraper:
    """
    A robust web scraper that uses Playwright to fetch and render web pages,
    and BeautifulSoup to parse the content. It is designed to handle
    JavaScript-heavy sites, take screenshots for visual context, and extract
    text, media, and metadata.
    """

    async def _launch_browser(self, p):
        """
        Launches a Playwright browser instance, potentially with extensions.
        """
        extensions_path = Config.PLAYWRIGHT_EXTENSIONS_PATH
        persistent_context_path = Config.PLAYWRIGHT_BROWSER_PATH

        if extensions_path and os.path.exists(extensions_path):
            extension_dirs = [
                os.path.join(extensions_path, d)
                for d in os.listdir(extensions_path)
                if os.path.isdir(os.path.join(extensions_path, d))
            ]
            if extension_dirs:
                logger.debug(f"Loading browser extensions from: {extension_dirs}")
                load_extensions_arg = ",".join(extension_dirs)
                context = await p.chromium.launch_persistent_context(
                    persistent_context_path,
                    headless=True,
                    args=[
                        f"--disable-extensions-except={load_extensions_arg}",
                        f"--load-extension={load_extensions_arg}",
                    ],
                )

                extension_ids = {}
                for page in context.background_pages:
                    extension_id = page.url.split("/")

                    for ext_dir in extension_dirs:
                        manifest_path = os.path.join(ext_dir, "manifest.json")
                        if os.path.exists(manifest_path):
                            with open(manifest_path, "r") as f:
                                import json

                                manifest = json.load(f)
                                if (
                                    manifest.get("name")
                                    == "uBlock Origin development build"
                                ):
                                    extension_ids["uBlock0.chromium"] = extension_id
                                elif manifest.get("name") == "Consent-O-Matic":
                                    extension_ids["consent-o-matic"] = extension_id

                if extension_ids:
                    extension_manager = ExtensionManager(context, extension_ids)
                    await extension_manager.configure_extensions()
            else:
                logger.debug(
                    "No extension directories found, launching without extensions."
                )
                context = await p.chromium.launch_persistent_context(
                    persistent_context_path, headless=True
                )
        else:
            logger.debug(
                "No valid extensions path found, launching without extensions."
            )
            context = await p.chromium.launch_persistent_context(
                persistent_context_path, headless=True
            )
        return context

    async def scrape(
        self, url: str, screenshot: bool = True, retries: int = 3
    ) -> Optional[ScrapedData]:
        """
        Scrapes a given URL with retries and returns a ScrapedData object.

        Args:
            url: The URL to scrape.
            screenshot: Whether to take a screenshot of the page.
            retries: The number of times to retry scraping on failure.

        Returns:
            A ScrapedData object containing the scraped information, or None if scraping fails.
        """
        logger.info(f"Attempting to scrape URL: {url} with {retries} retries.")
        for attempt in range(retries):
            try:
                async with async_playwright() as p:
                    context = await self._launch_browser(p)
                    page = await context.new_page()
                    await page.goto(url, wait_until="networkidle", timeout=30000)

                    content = await page.content()
                    screenshot_bytes = (
                        await page.screenshot(full_page=True) if screenshot else None
                    )
                    resolved_url = page.url

                    await context.close()

                    soup = BeautifulSoup(content, "html.parser")

                    title = soup.title.string if soup.title else "No title found"

                    for script_or_style in soup(["script", "style"]):
                        script_or_style.decompose()
                    text_content = " ".join(soup.stripped_strings)

                    logger.info(f"Successfully scraped {url} on attempt {attempt + 1}.")
                    return ScrapedData(
                        resolved_url=resolved_url,
                        title=title,
                        text_content=text_content,
                        screenshot_data=screenshot_bytes,
                        timestamp=time.time(),
                        media=[],
                        video_details=None,
                    )
            except Error as e:
                logger.warning(
                    f"Playwright error on attempt {attempt + 1} for {url}: {e}"
                )
                if attempt < retries - 1:
                    await asyncio.sleep(2**attempt)
                else:
                    logger.error(f"All {retries} attempts to scrape {url} failed.")
                    return None
            except Exception as e:
                logger.error(
                    f"Unexpected error on attempt {attempt + 1} for {url}: {e}"
                )
                return None
        return None
