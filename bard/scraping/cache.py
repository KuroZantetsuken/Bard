import binascii
import hashlib
import json
import logging
import time
from copy import deepcopy
from pathlib import Path
from typing import Any, Optional
from urllib.parse import urlparse

from bard.scraping.models import CachedObject, ScrapedData, ScrapedMedia, VideoDetails
from config import Config

logger = logging.getLogger("Bard")


class CacheManager:
    """
    Manages a filesystem-based cache for scraped web content, storing and
    retrieving CachedObject instances which wrap ScrapedData.
    """

    def __init__(self, cache_duration: int = 3600):
        self.cache_dir = Path(Config.CACHE_DIR)
        self.cache_duration = cache_duration
        self.cache_dir.mkdir(parents=True, exist_ok=True)
        logger.info(
            f"CacheManager initialized at {self.cache_dir} with duration {self.cache_duration}s."
        )

    def _get_cache_path(self, resolved_url: str) -> Path:
        """
        Generates a cache file path from a resolved URL using an MD5 hash
        and organizes caches into domain-specific subdirectories.
        """
        try:
            domain = urlparse(resolved_url).netloc
            if not domain:
                domain = "misc"
        except Exception:
            domain = "misc"

        domain_dir = self.cache_dir / domain
        domain_dir.mkdir(parents=True, exist_ok=True)

        url_hash = hashlib.md5(resolved_url.encode()).hexdigest()
        return domain_dir / f"{url_hash}.json"

    def get(self, resolved_url: str) -> Optional[ScrapedData]:
        """
        Retrieves a ScrapedData object from the cache if a valid, unexpired
        CachedObject exists for the given resolved URL.

        Args:
            resolved_url: The resolved URL to retrieve from the cache.

        Returns:
            The cached ScrapedData, or None if not found or expired.
        """
        cache_path = self._get_cache_path(resolved_url)
        if not cache_path.exists():
            logger.info(f"Cache miss for URL: {resolved_url}")
            return None

        try:
            with open(cache_path, "r") as f:
                cached_obj_dict = json.load(f)

            cached_obj = self._deserialize_cached_object(cached_obj_dict)

            if cached_obj.expires > time.time():
                logger.info(f"Cache hit for URL: {resolved_url}")
                return cached_obj.data
            else:
                logger.info(f"Cache expired for URL: {resolved_url}. Deleting.")
                cache_path.unlink()
                screenshot_path = cache_path.with_suffix(".png")
                if screenshot_path.exists():
                    screenshot_path.unlink()
                return None
        except (json.JSONDecodeError, TypeError, binascii.Error, KeyError) as e:
            logger.warning(f"Failed to decode cache for {resolved_url}: {e}. Deleting.")
            cache_path.unlink()
            screenshot_path = cache_path.with_suffix(".png")
            if screenshot_path.exists():
                screenshot_path.unlink()
            return None

    def set(self, resolved_url: str, data: ScrapedData):
        """
        Serializes a ScrapedData object into a CachedObject and stores it
        in the cache, saving the screenshot as a separate file.
        """
        cache_path = self._get_cache_path(resolved_url)
        expires = time.time() + self.cache_duration

        data_for_serialization = deepcopy(data)

        if data.screenshot_data and isinstance(data.screenshot_data, bytes):
            screenshot_path = cache_path.with_suffix(".png")
            try:
                with open(screenshot_path, "wb") as f:
                    f.write(data.screenshot_data)

                data_for_serialization.screenshot_data = str(screenshot_path)  # type: ignore
            except IOError as e:
                logger.error(f"Failed to save screenshot for {resolved_url}: {e}")
                data_for_serialization.screenshot_data = None
        else:
            data_for_serialization.screenshot_data = None

        cached_obj = CachedObject(data=data_for_serialization, expires=expires)

        try:
            with open(cache_path, "w") as f:
                json.dump(cached_obj, f, cls=self.CacheEncoder, indent=4)
            logger.info(f"Successfully cached data for URL: {resolved_url}")
        except TypeError as e:
            logger.error(f"Failed to serialize data for {resolved_url}: {e}")

    def _deserialize_cached_object(self, obj_dict: dict) -> CachedObject:
        """Helper to reconstruct a CachedObject from a dictionary."""
        data_dict = obj_dict["data"]

        if data_dict.get("screenshot_data") and isinstance(
            data_dict["screenshot_data"], str
        ):
            screenshot_path = Path(data_dict["screenshot_data"])
            if screenshot_path.exists():
                try:
                    with open(screenshot_path, "rb") as f:
                        data_dict["screenshot_data"] = f.read()
                except IOError as e:
                    logger.error(f"Failed to load screenshot {screenshot_path}: {e}")
                    data_dict["screenshot_data"] = None
            else:
                logger.warning(f"Screenshot file not found at {screenshot_path}")
                data_dict["screenshot_data"] = None

        data_dict["media"] = [ScrapedMedia(**m) for m in data_dict.get("media", [])]
        if data_dict.get("video_details"):
            data_dict["video_details"] = VideoDetails(**data_dict["video_details"])

        scraped_data = ScrapedData(**data_dict)
        return CachedObject(data=scraped_data, expires=obj_dict["expires"])

    class CacheEncoder(json.JSONEncoder):
        """
        Custom JSON encoder to handle serialization of ScrapedData and other
        custom objects. Bytes are not expected here as they are handled before encoding.
        """

        def default(self, o: Any) -> Any:
            if isinstance(o, (ScrapedData, CachedObject, VideoDetails, ScrapedMedia)):
                return o.__dict__
            if isinstance(o, bytes):
                logger.warning("CacheEncoder encountered unexpected bytes data.")
                return None
            return super().default(o)
