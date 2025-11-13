import asyncio
import json
import sys

from playwright.async_api import async_playwright


async def main():
    if len(sys.argv) < 2:
        print("Usage: python src/debug_scraper.py <search_query>")
        return

    search_query = " ".join(sys.argv[1:])

    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        page = await browser.new_page()

        url = (
            f"https://www.google.com/search?q={search_query.replace(' ', '+')}&tbm=isch"
        )
        print(f"Navigating to {url}")

        await page.goto(url, wait_until="networkidle")

        screenshot_path = "page_screenshot.png"
        await page.screenshot(path=screenshot_path)
        print(f"Screenshot saved to {screenshot_path}")

        image_data = await page.evaluate("""() => {
            const images = Array.from(document.querySelectorAll('img'));
            return images.map(img => ({
                src: img.src,
                alt: img.alt,
                width: img.width,
                height: img.height,
                parent_tag: img.parentElement ? img.parentElement.tagName : null,
                parent_classes: img.parentElement ? img.parentElement.className : null,
            }));
        }""")

        json_path = "image_data.json"
        with open(json_path, "w", encoding="utf-8") as f:
            json.dump(image_data, f, indent=2)
        print(f"Image data saved to {json_path}")

        await browser.close()

        print("\nDebugging files created:")
        print(f"- {screenshot_path}")
        print(f"- {json_path}")


if __name__ == "__main__":
    asyncio.run(main())
