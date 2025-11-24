import asyncio
import logging
import time
from typing import List, Optional

from scraper.cache import CacheManager
from scraper.image import ImageScraper
from scraper.models import ResolvedURL, ScrapedData
from scraper.scraper import Scraper
from scraper.video import VideoHandler

log = logging.getLogger("Bard")


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
        image_scraper: ImageScraper,
    ):
        self.cache_manager = cache_manager
        self.scraper = scraper
        self.video_handler = video_handler
        self.image_scraper = image_scraper

    async def process_urls(self, urls: List[str]) -> List[ScrapedData]:
        """
        Processes a list of URLs concurrently, either by fetching from cache
        or by scraping.
        """
        log.debug("Processing URLs.", extra={"url_count": len(urls)})
        tasks = [self.process_url(url) for url in urls]
        results = await asyncio.gather(*tasks)
        log.debug("Finished processing URLs.", extra={"url_count": len(urls)})
        return [data for data in results if data]

    async def process_url(self, url: str) -> Optional[ScrapedData]:
        page = None
        try:
            resolved_url, page = await self.scraper.resolve_url_and_get_page(url)
            url_obj = ResolvedURL(original=url, resolved=resolved_url)

            cached_data = await self.cache_manager.get_from_cache(url_obj)
            if cached_data:
                log.debug(
                    "Cache hit for resolved URL.",
                    extra={
                        "original": url_obj.original,
                        "resolved": url_obj.resolved,
                    },
                )
                return cached_data

            log.info(
                "Cache miss, proceeding with live processing.",
                extra={"url": url_obj.resolved},
            )

            scrape_task = asyncio.create_task(self.scraper.scrape(url_obj, page))
            video_task = asyncio.create_task(self.video_handler.process_url(url_obj))

            scraped_data, video_details = await asyncio.gather(scrape_task, video_task)

            if not scraped_data:
                log.warning(
                    "Scraping failed, proceeding with video details if available.",
                    extra={"url": url_obj.resolved},
                )
                if video_details and video_details.is_video:
                    scraped_data = ScrapedData(
                        url=url_obj,
                        title=(
                            video_details.metadata.get("title", "Untitled Video")
                            if video_details.metadata
                            else "Untitled Video"
                        ),
                        text_content="",
                        screenshot_data=None,
                        timestamp=time.time(),
                        video_details=video_details,
                    )
                else:
                    log.error(
                        "Failed to process URL after all attempts.", extra={"url": url}
                    )
                    return None
            elif video_details and video_details.is_video:
                scraped_data.video_details = video_details

            await self.cache_manager.set_to_cache(scraped_data)
            log.info(
                "Successfully processed and cached URL.",
                extra={"url": scraped_data.url.resolved},
            )
            return scraped_data
        finally:
            if page:
                await page.close()

