import logging

from playwright.async_api import BrowserContext

logger = logging.getLogger("Bard")


class ExtensionManager:
    """
    Manages the configuration of browser extensions in Playwright.
    """

    def __init__(self, context: BrowserContext, extension_ids: dict):
        self.context = context
        self.extension_ids = extension_ids

    async def configure_extensions(self):
        """
        Configures all loaded extensions.
        """
        await self._configure_ublock_origin()
        await self._configure_consent_o_matic()

    async def _configure_ublock_origin(self):
        """
        Configures uBlock Origin to be more aggressive.
        """
        ublock_id = self.extension_ids.get("uBlock0.chromium")
        if not ublock_id:
            logger.warning("uBlock Origin extension not found.")
            return

        logger.info("Configuring uBlock Origin...")
        page = await self.context.new_page()
        try:
            await page.goto(
                f"chrome-extension://{ublock_id}/dashboard.html#3p-filters.html"
            )
            await page.wait_for_load_state("networkidle")

            annoyance_filters = [
                "adguard-annoyance",
                "fanboy-annoyance",
                "ublock-annoyances",
            ]
            for filter_id in annoyance_filters:
                await page.check(f"input[data-listkey='{filter_id}']")
                logger.debug(f"Enabled uBlock Origin filter: {filter_id}")

            await page.click("#applyChanges")
            logger.info("uBlock Origin configured successfully.")
        except Exception as e:
            logger.error(f"Failed to configure uBlock Origin: {e}")
        finally:
            await page.close()

    async def _configure_consent_o_matic(self):
        """
        Configures Consent-O-Matic to be more aggressive.
        """
        consent_o_matic_id = self.extension_ids.get("consent-o-matic")
        if not consent_o_matic_id:
            logger.warning("Consent-O-Matic extension not found.")
            return

        logger.info("Configuring Consent-O-Matic...")
        page = await self.context.new_page()
        try:
            await page.goto(f"chrome-extension://{consent_o_matic_id}/options.html")
            await page.wait_for_load_state("networkidle")

            await page.evaluate(
                "() => { document.querySelectorAll('select').forEach(s => s.value = 'opt-out'); }"
            )
            logger.info("Consent-O-Matic configured successfully.")
        except Exception as e:
            logger.error(f"Failed to configure Consent-O-Matic: {e}")
        finally:
            await page.close()
