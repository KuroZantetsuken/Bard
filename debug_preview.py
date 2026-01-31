import asyncio
import logging
import os
import sys
import json
from urllib.parse import quote_plus

# Add src to sys.path to allow imports
sys.path.append(os.path.join(os.getcwd(), "src"))

from scraper.scraper import Scraper

async def debug_image_preview(query: str) -> None:
    # Setup logging
    logging.basicConfig(level=logging.DEBUG)
    
    scraper = Scraper()
    if not scraper._browser:
        await scraper.launch_browser()

    if not scraper._browser:
        print("Failed to launch browser")
        return

    page = await scraper._browser.new_page()
    
    try:
        encoded_search_terms = quote_plus(query)
        url = f"https://www.google.com/search?q={encoded_search_terms}&tbm=isch"
        await page.goto(url, wait_until="networkidle")

        images = await page.query_selector_all("img")
        thumbnail_to_click = None
        for img in images:
            src = await img.get_attribute("src")
            if src and src.startswith("data:image"):
                box = await img.bounding_box()
                if box and box["width"] > 50 and box["height"] > 50:
                    thumbnail_to_click = img
                    break

        if thumbnail_to_click:
            await thumbnail_to_click.click()
            await asyncio.sleep(2) # Wait for preview to open
            
            preview_images = await page.evaluate("""() => {
                const imgs = Array.from(document.querySelectorAll('img'));
                return imgs.map(img => {
                    const rect = img.getBoundingClientRect();
                    
                    let parents = [];
                    let p = img.parentElement;
                    for (let i = 0; i < 5 && p; i++) {
                        parents.push({
                            tag: p.tagName,
                            class: p.className,
                            id: p.id,
                            role: p.getAttribute('role'),
                            ariaLabel: p.getAttribute('aria-label')
                        });
                        p = p.parentElement;
                    }

                    return {
                        src: img.src ? img.src.substring(0, 100) : null,
                        width: rect.width,
                        height: rect.height,
                        alt: img.alt,
                        classes: img.className,
                        id: img.id,
                        jsname: img.getAttribute('jsname'),
                        isVisible: rect.width > 0 && rect.height > 0,
                        parents: parents
                    };
                }).filter(img => img.isVisible && img.width > 200);
            }""")
            
            print(json.dumps(preview_images, indent=2))
            
    finally:
        if scraper._browser:
            await scraper._browser.close()
        if scraper._playwright:
            await scraper._playwright.stop()

if __name__ == "__main__":
    query = "apple"
    asyncio.run(debug_image_preview(query))
