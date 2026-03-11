import asyncio
import logging
import os
import sys

sys.path.append(os.path.join(os.getcwd(), "src"))
from scraper.image import ImageScraper
from scraper.scraper import Scraper


async def test_image_scraper(query: str) -> None:

    logging.basicConfig(level=logging.DEBUG)

    scraper = Scraper()
    image_scraper = ImageScraper(scraper)

    try:
        data = await image_scraper.scrape_image_data(query)
        if data:
            with open("test_image.jpg", "wb") as f:
                f.write(data)
            print(f"Successfully scraped image for '{query}' and saved to test_image.jpg")
        else:
            print(f"Failed to scrape image for '{query}'")
    finally:
        if scraper._browser:
            await scraper._browser.close()
        if scraper._playwright:
            await scraper._playwright.stop()


if __name__ == "__main__":
    query = "cute puppy" if len(sys.argv) < 2 else sys.argv[1]
    asyncio.run(test_image_scraper(query))
