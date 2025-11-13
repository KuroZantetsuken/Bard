import asyncio
import logging
from io import BytesIO

from PIL import Image, ImageChops
from playwright.async_api import Page

log = logging.getLogger("Bard")


class PageStability:
    """
    A class to determine if a web page is stable by comparing screenshots.
    """

    def __init__(
        self,
        page: Page,
        stability_threshold: float = 0.99,
        check_interval: int = 1,
        required_stable_duration: int = 3,
    ):
        self._page = page
        self._stability_threshold = stability_threshold
        self._check_interval = check_interval
        self._required_stable_duration = required_stable_duration

    async def _take_screenshot(self) -> Image.Image:
        """Takes a screenshot and returns it as a PIL Image."""
        screenshot_bytes = await self._page.screenshot(full_page=True)
        return Image.open(BytesIO(screenshot_bytes)).convert("RGB")

    def _compare_images(self, img1: Image.Image, img2: Image.Image) -> float:
        """Compares two images and returns a similarity score."""
        if img1.size != img2.size:
            log.debug(
                "Image sizes do not match, cropping to the smaller size.",
                extra={"size1": img1.size, "size2": img2.size},
            )

            if img1.width * img1.height > img2.width * img2.height:
                img1 = img1.crop((0, 0, img2.width, img2.height))
            else:
                img2 = img2.crop((0, 0, img1.width, img1.height))

        diff = ImageChops.difference(img1, img2)
        diff = diff.convert("L")
        total_pixels = img1.width * img1.height
        changed_pixels = sum(1 for pixel in diff.getdata() if pixel != 0)

        return 1.0 - (changed_pixels / total_pixels)

    async def wait_for_stable_page(self, timeout: int = 30):
        """
        Waits for the page to become visually stable by comparing screenshots.
        """
        log.debug("Waiting for page to stabilize.")
        start_time = asyncio.get_event_loop().time()
        last_screenshot = await self._take_screenshot()
        stable_since = None

        while (asyncio.get_event_loop().time() - start_time) < timeout:
            await asyncio.sleep(self._check_interval)
            current_screenshot = await self._take_screenshot()

            similarity = self._compare_images(last_screenshot, current_screenshot)
            log.debug(
                "Page stability check.",
                extra={
                    "similarity": f"{similarity:.4f}",
                    "threshold": self._stability_threshold,
                },
            )

            if similarity >= self._stability_threshold:
                if stable_since is None:
                    stable_since = asyncio.get_event_loop().time()
                elif (
                    asyncio.get_event_loop().time() - stable_since
                    >= self._required_stable_duration
                ):
                    log.info("Page is visually stable.")
                    return
            else:
                stable_since = None

            last_screenshot = current_screenshot

        log.warning(
            "Page did not stabilize within timeout.", extra={"timeout": timeout}
        )
