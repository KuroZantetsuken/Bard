import asyncio
import logging
import os

from playwright.async_api import async_playwright

logging.basicConfig(
    level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s"
)
logger = logging.getLogger("BardSetup")


async def main():
    """
    This script provides an optional, one-time setup for pre-configuring the browser.

    It is used when you want to customize the browser with specific settings,
    extensions, or other configurations before running the main application.
    If you do not run this setup, the project will create its own default
    browser instance when needed.

    This script launches a headed Chromium browser, allowing you to manually
    install extensions and adjust settings. Once you close the browser, your
    configurations are saved to the `data/browser` directory, which will then
    be used by the application.

    Instructions:
    1. Run this script on a local machine with a GUI using `python setup_browser.py`.
    2. A Chromium browser window will open.
    3. Manually configure any extensions or settings you need.
    4. Close the browser window when you are finished.
    5. The `data/browser` directory now contains your custom browser profile.
       If running the main application on a different machine (e.g., a headless
       server), transfer this directory to it.

    The resulting `data/browser` directory acts as a "golden copy" of your
    custom browser configuration for all subsequent scraping tasks.
    """
    logger.info("Starting optional browser pre-configuration setup...")

    extensions_path = "extensions"
    persistent_context_path = "data"
    user_agent = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/119.0.0.0 Safari/537.36"

    if not os.path.exists(extensions_path):
        logger.error(
            f"Extensions path not found at '{extensions_path}'. Please ensure it exists."
        )
        return

    extension_dirs = [
        os.path.join(extensions_path, d)
        for d in os.listdir(extensions_path)
        if os.path.isdir(os.path.join(extensions_path, d))
    ]

    if not extension_dirs:
        logger.error(f"No extension directories found in '{extensions_path}'.")
        return

    disable_extensions_arg = ",".join(extension_dirs)
    load_extension_args = [f"--load-extension={d}" for d in extension_dirs]
    browser_args = [
        f"--disable-extensions-except={disable_extensions_arg}"
    ] + load_extension_args

    logger.debug(f"Browser launch arguments: {browser_args}")
    logger.info("Launching headed browser to install and configure extensions.")
    logger.info(f"Browser data will be saved to: {persistent_context_path}")
    logger.info("Please wait for the browser to open...")

    async with async_playwright() as p:
        context = await p.chromium.launch_persistent_context(
            persistent_context_path,
            headless=False,  
            user_agent=user_agent,
            args=browser_args,
        )

        
        page = await context.new_page()
        await page.goto("about:blank")

        logger.info("Browser is now open. Please perform any manual extension setup.")
        logger.info("Close the browser window when you are finished.")

        
        await context.wait_for_event("close")

    logger.info("Browser closed. Pre-configuration is complete.")
    logger.info(
        f"The user data directory at '{persistent_context_path}' is now configured with your custom settings."
    )


if __name__ == "__main__":
    asyncio.run(main())
