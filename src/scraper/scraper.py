import asyncio
import logging
import os
import time
from typing import Optional

from bs4 import BeautifulSoup
from playwright.async_api import (
    BrowserContext,
    Error,
    Page,
    Playwright,
    async_playwright,
)

from retry import async_retry
from scraper.models import ResolvedURL, ScrapedData
from scraper.page import PageStability
from settings import Settings

log = logging.getLogger("Bard")


class Scraper:
    def __init__(self):
        self._playwright: Playwright | None = None
        self._browser: BrowserContext | None = None
        self._lock = asyncio.Lock()

    async def __aenter__(self):
        await self.launch_browser()
        return self

    async def __aexit__(self, exc_type, exc_val, exc_tb):
        await self.close()

    async def launch_browser(self):
        async with self._lock:
            if not self._browser:
                log.info("Launching browser...")
                self._playwright = await async_playwright().__aenter__()
                self._browser = await self._launch_persistent_browser(self._playwright)
                log.info("Browser launched successfully.")

    async def _launch_persistent_browser(self, p: Playwright) -> BrowserContext:
        persistent_context_path = Settings.PLAYWRIGHT_BROWSER_PATH

        singleton_lock_path = os.path.join(persistent_context_path, "SingletonLock")
        if os.path.exists(singleton_lock_path):
            log.debug(
                "Removing existing SingletonLock file.",
                extra={"path": singleton_lock_path},
            )
            try:
                os.remove(singleton_lock_path)
            except OSError as e:
                log.warning(
                    "Failed to remove SingletonLock file, attempting to proceed.",
                    extra={"error": str(e)},
                )

        extensions_path = Settings.PLAYWRIGHT_EXTENSIONS_PATH
        user_agent = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/119.0.0.0 Safari/537.36"

        browser_args = []
        if os.path.exists(extensions_path):
            extension_dirs = [
                os.path.join(extensions_path, d)
                for d in os.listdir(extensions_path)
                if os.path.isdir(os.path.join(extensions_path, d))
            ]
            if extension_dirs:
                extension_args = ",".join(extension_dirs)
                browser_args.extend(
                    [
                        f"--disable-extensions-except={extension_args}",
                        f"--load-extension={extension_args}",
                    ]
                )
                log.debug(
                    "Found extensions, launching with args.",
                    extra={"playwright_args": browser_args},
                )

        if "--headless=new" not in browser_args:
            browser_args.append("--headless=new")

        log.debug(
            "Launching browser with persistent context.",
            extra={"path": persistent_context_path},
        )
        try:
            context = await p.chromium.launch_persistent_context(
                persistent_context_path,
                headless=False,
                channel="chromium",
                user_agent=user_agent,
                args=browser_args,
            )
            log.debug("Browser launched successfully with pre-configured profile.")
            return context
        except Error as e:
            log.error(
                "Failed to launch Playwright with persistent context.",
                extra={"error": str(e)},
            )
            log.error(
                "Please ensure the browser profile in 'data/browser' is not corrupt and was generated with a compatible browser version."
            )
            raise

    async def close(self):
        async with self._lock:
            if self._browser:
                await self._browser.close()
                self._browser = None
            if self._playwright:
                await self._playwright.stop()
                self._playwright = None
            log.info("Browser closed.")

    @async_retry(retry_on=(Error,), retries=3, delay=1, backoff=2)
    async def resolve_url_and_get_page(self, url: str) -> tuple[str, "Page"]:
        if not self._browser:
            await self.launch_browser()
        assert self._browser is not None

        page = await self._browser.new_page()
        try:
            await page.goto(url, wait_until="commit")
            return page.url, page
        except Exception:
            await page.close()
            raise

    async def scrape(
        self,
        url_obj: ResolvedURL,
        page: "Page",
        screenshot: bool = True,
        retries: int = 3,
    ) -> Optional[ScrapedData]:
        try:
            for attempt in range(retries):
                try:
                    log.debug(
                        "Navigating to URL.",
                        extra={"url": url_obj.resolved, "attempt": attempt + 1},
                    )
                    await page.goto(
                        url_obj.resolved,
                        wait_until="load",
                        timeout=Settings.TOOL_TIMEOUT_SECONDS * 1000,
                    )

                    page_stability = PageStability(
                        page,
                        stability_threshold=0.95,
                        check_interval=1,
                        required_stable_duration=2,
                    )
                    await page_stability.wait_for_stable_page(
                        timeout=Settings.TOOL_TIMEOUT_SECONDS
                    )

                    content = await page.content()
                    screenshot_bytes = None
                    if screenshot:
                        log.debug("Taking screenshot.", extra={"url": url_obj.resolved})
                        page_size = await page.evaluate(
                            """() => ({
                            width: Math.max(
                                document.body.scrollWidth, document.documentElement.scrollWidth,
                                document.body.offsetWidth, document.documentElement.offsetWidth,
                                document.body.clientWidth, document.documentElement.clientWidth
                            ),
                            height: Math.max(
                                document.body.scrollHeight, document.documentElement.scrollHeight,
                                document.body.offsetHeight, document.documentElement.offsetHeight,
                                document.body.clientHeight, document.documentElement.clientHeight
                            )
                        })"""
                        )
                        await page.set_viewport_size(page_size)
                        screenshot_bytes = await page.screenshot()
                        log.debug("Screenshot taken.", extra={"url": url_obj.resolved})

                    soup = BeautifulSoup(content, "html.parser")
                    title = soup.title.string if soup.title else "No title found"
                    for script_or_style in soup(["script", "style"]):
                        script_or_style.decompose()
                    text_content = " ".join(soup.stripped_strings)

                    log.debug(
                        "Successfully scraped URL.",
                        extra={"url": url_obj.resolved, "attempt": attempt + 1},
                    )
                    return ScrapedData(
                        url=url_obj,
                        title=title,
                        text_content=text_content,
                        screenshot_data=screenshot_bytes,
                        timestamp=time.time(),
                        media=[],
                        video_details=None,
                    )
                except Error as e:
                    log.warning(
                        "Playwright error during scrape attempt.",
                        extra={
                            "url": url_obj.resolved,
                            "attempt": attempt + 1,
                            "error": str(e),
                        },
                    )
                    if attempt < retries - 1:
                        await asyncio.sleep(2**attempt)
                    else:
                        log.error(
                            "All scrape attempts failed due to Playwright errors.",
                            extra={"url": url_obj.resolved, "retries": retries},
                        )
        except Exception as e:
            log.error(
                "An unexpected error occurred while scraping.",
                extra={"url": url_obj.resolved, "error": str(e)},
                exc_info=True,
            )

        return None
