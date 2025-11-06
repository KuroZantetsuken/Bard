import asyncio
import logging
import time
from typing import List, Optional

import aiohttp

from bard.scraping.cache import CacheManager
from bard.scraping.models import ScrapedData
from bard.scraping.scraper import Scraper
from bard.scraping.video import VideoHandler

logger = logging.getLogger("Bard")


class ScrapingOrchestrator:
    """
    Orchestrates the web scraping process by coordinating video detection,
    scraping, and caching. It ensures that URLs are processed efficiently,
    with a "video-first" approach.
    """

    def __init__(
        self,
        cache_manager: CacheManager,
        scraper: Scraper,
        video_handler: VideoHandler,
    ):
        self.cache_manager = cache_manager
        self.scraper = scraper
        self.video_handler = video_handler

    async def _resolve_url(self, url: str) -> str:
        """Resolves a URL to its final destination after any redirects."""
        try:
            timeout = aiohttp.ClientTimeout(total=15)
            async with aiohttp.ClientSession() as session:
                async with session.head(
                    url, allow_redirects=True, timeout=timeout
                ) as response:
                    resolved_url = str(response.url)
                    if url != resolved_url:
                        logger.info(f"Resolved URL {url} to {resolved_url}")
                    return resolved_url
        except Exception as e:
            logger.warning(
                f"Could not resolve URL {url} due to error: {e}. Using original URL."
            )
            return url

    async def process_urls(self, urls: List[str]) -> List[ScrapedData]:
        """
        Processes a list of URLs concurrently, either by fetching from cache
        or by scraping.
        """
        tasks = [self.process_url(url) for url in urls]
        results = await asyncio.gather(*tasks)
        return [data for data in results if data]

    async def process_grounding_urls(self, urls: List[str]) -> List[ScrapedData]:
        """
        Processes a list of grounding source URLs from a search tool call.
        This is meant to provide additional visual context (screenshots) for the AI.
        """
        logger.info(f"Processing {len(urls)} grounding source URLs.")
        return await self.process_urls(urls)

    async def process_url(self, url: str) -> Optional[ScrapedData]:
        """
        Processes a single URL by first resolving redirects, then checking the cache,
        and finally delegating to the scraper and video handler if no cached
        data is found.

        The end-to-end flow is:
        1. Resolve URL redirects.
        2. Check the cache for the resolved URL.
        3. If a valid cache entry is found, return it.
        4. Concurrently run the scraper and video handler.
        5. Combine the results and cache them.
        6. Return the combined scraped data.
        """

        resolved_url = await self._resolve_url(url)

        cached_data = self.cache_manager.get(resolved_url)
        if cached_data:
            logger.info(
                f"Cache hit for resolved URL: {resolved_url} (from original: {url})"
            )
            return cached_data

        logger.info(f"Cache miss for {resolved_url}, proceeding with live processing.")

        scrape_task = asyncio.create_task(self.scraper.scrape(resolved_url))
        video_task = asyncio.create_task(self.video_handler.process_url(resolved_url))

        scraped_data, video_details = await asyncio.gather(scrape_task, video_task)

        if not scraped_data:
            logger.warning(
                f"Scraping failed for {resolved_url}, but proceeding with video details if available."
            )

            if video_details and video_details.is_video:
                scraped_data = ScrapedData(
                    resolved_url=resolved_url,
                    title=video_details.metadata.get("title", "Untitled Video")
                    if video_details.metadata
                    else "Untitled Video",
                    text_content="",
                    screenshot_data=None,
                    timestamp=time.time(),
                    video_details=video_details,
                )
            else:
                logger.error(f"Failed to process URL after all attempts: {url}")
                return None
        else:
            if video_details and video_details.is_video:
                scraped_data.video_details = video_details
            else:
                scraped_data.video_details = None

        self.cache_manager.set(scraped_data.resolved_url, scraped_data)
        logger.info(
            f"Successfully processed and cached URL: {scraped_data.resolved_url}"
        )

        return scraped_data
