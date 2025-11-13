import binascii
import hashlib
import json
import logging
import time
from copy import deepcopy
from pathlib import Path
from typing import Any, Optional
from urllib.parse import urlparse

from scraper.models import (
    CachedObject,
    ResolvedURL,
    ScrapedData,
    ScrapedMedia,
    VideoDetails,
)
from settings import Settings

log = logging.getLogger("Bard")


class CacheManager:
    """
    Manages a filesystem-based cache for scraped web content, storing and
    retrieving CachedObject instances which wrap ScrapedData.
    """

    def __init__(self, cache_duration: int = 3600):
        self.cache_dir = Path(Settings.CACHE_DIR)
        self.cache_duration = cache_duration
        self.cache_dir.mkdir(parents=True, exist_ok=True)
        log.info(
            "CacheManager initialized.",
            extra={"cache_dir": str(self.cache_dir), "duration": self.cache_duration},
        )

    def _get_cache_path(self, resolved_url: str) -> Path:
        """
        Generates a cache file path from a resolved URL using an MD5 hash
        and organizes caches into domain-specific subdirectories.
        """
        log.debug("Generating cache path for URL.", extra={"url": resolved_url})
        try:
            domain = urlparse(resolved_url).netloc
            if not domain:
                domain = "misc"
        except Exception:
            domain = "misc"

        domain_dir = self.cache_dir / domain
        domain_dir.mkdir(parents=True, exist_ok=True)

        url_hash = hashlib.md5(resolved_url.encode()).hexdigest()
        path = domain_dir / f"{url_hash}.json"
        log.debug(
            "Generated cache path.", extra={"url": resolved_url, "path": str(path)}
        )
        return path

    def get_cache_base_path_for_url(self, resolved_url: str) -> Path:
        """
        Generates a base cache file path (without extension) from a resolved URL.
        """
        cache_path = self._get_cache_path(resolved_url)
        return cache_path.with_suffix("")

    def get_video_path(self, resolved_url: str) -> Optional[Path]:
        """
        Finds a cached video file for a given URL by searching for a file
        starting with the URL's hash, ignoring the extension.
        """
        log.debug("Searching for cached video.", extra={"url": resolved_url})
        base_path = self.get_cache_base_path_for_url(resolved_url)
        parent_dir = base_path.parent
        file_hash = base_path.name

        if not parent_dir.exists():
            log.debug(
                "Cache directory does not exist for video search.",
                extra={"url": resolved_url, "dir": str(parent_dir)},
            )
            return None

        for file in parent_dir.iterdir():
            if file.stem == file_hash and file.suffix != ".json":
                log.debug(
                    "Found cached video file.",
                    extra={"url": resolved_url, "path": str(file)},
                )
                return file

        log.debug("No cached video found for URL.", extra={"url": resolved_url})
        return None

    def get_from_cache(self, url_obj: ResolvedURL) -> Optional[ScrapedData]:
        """
        Retrieves a ScrapedData object from the cache if a valid, unexpired
        CachedObject exists for the given resolved URL.

        Args:
            resolved_url: The resolved URL to retrieve from the cache.

        Returns:
            The cached ScrapedData, or None if not found or expired.
        """
        cache_path = self._get_cache_path(url_obj.resolved)
        if not cache_path.exists():
            log.debug("Cache miss for URL.", extra={"url": url_obj.resolved})
            return None

        try:
            with open(cache_path, "r") as f:
                cached_obj_dict = json.load(f)

            cached_obj = self._deserialize_cached_object(cached_obj_dict)

            if cached_obj.expires > time.time():
                log.info("Cache hit for URL.", extra={"url": url_obj.resolved})
                return cached_obj.data
            else:
                log.info(
                    "Cache expired for URL. Deleting.", extra={"url": url_obj.resolved}
                )
                cache_path.unlink()
                screenshot_path = cache_path.with_suffix(".png")
                if screenshot_path.exists():
                    screenshot_path.unlink()
                return None
        except (json.JSONDecodeError, TypeError, binascii.Error, KeyError) as e:
            log.warning(
                "Failed to decode cache. Deleting.",
                extra={"url": url_obj.resolved, "error": str(e)},
            )
            cache_path.unlink()
            screenshot_path = cache_path.with_suffix(".png")
            if screenshot_path.exists():
                screenshot_path.unlink()
            return None

    def set_to_cache(self, data: ScrapedData):
        """
        Serializes a ScrapedData object into a CachedObject and stores it
        in the cache, saving the screenshot as a separate file.
        """
        resolved_url = data.url.resolved
        log.debug("Setting data to cache.", extra={"url": resolved_url})
        cache_path = self._get_cache_path(resolved_url)
        expires = time.time() + self.cache_duration

        data_for_serialization = deepcopy(data)

        if data.screenshot_data and isinstance(data.screenshot_data, bytes):
            screenshot_path = cache_path.with_suffix(".png")
            try:
                with open(screenshot_path, "wb") as f:
                    f.write(data.screenshot_data)

                data_for_serialization.screenshot_data = str(screenshot_path)  # type: ignore
                log.debug(
                    "Saved screenshot to cache.",
                    extra={"url": resolved_url, "path": str(screenshot_path)},
                )
            except IOError as e:
                log.error(
                    "Failed to save screenshot.",
                    extra={"url": resolved_url, "error": str(e)},
                )
                data_for_serialization.screenshot_data = None
        else:
            data_for_serialization.screenshot_data = None

        cached_obj = CachedObject(data=data_for_serialization, expires=expires)

        try:
            with open(cache_path, "w") as f:
                json.dump(cached_obj, f, cls=self.CacheEncoder, indent=4)
            log.debug("Successfully cached data for URL.", extra={"url": resolved_url})
        except TypeError as e:
            log.error(
                "Failed to serialize data for URL.",
                extra={"url": resolved_url, "error": str(e)},
            )

    def _deserialize_cached_object(self, obj_dict: dict) -> CachedObject:
        """Helper to reconstruct a CachedObject from a dictionary."""
        log.debug("Deserializing cached object.")
        data_dict = obj_dict["data"]

        if data_dict.get("screenshot_data") and isinstance(
            data_dict["screenshot_data"], str
        ):
            screenshot_path = Path(data_dict["screenshot_data"])
            if screenshot_path.exists():
                try:
                    with open(screenshot_path, "rb") as f:
                        data_dict["screenshot_data"] = f.read()
                    log.debug(
                        "Loaded screenshot from cache.",
                        extra={"path": str(screenshot_path)},
                    )
                except IOError as e:
                    log.error(
                        "Failed to load screenshot from cache.",
                        extra={"path": str(screenshot_path), "error": str(e)},
                    )
                    data_dict["screenshot_data"] = None
            else:
                log.warning(
                    "Screenshot file not found.", extra={"path": str(screenshot_path)}
                )
                data_dict["screenshot_data"] = None

        data_dict["media"] = [ScrapedMedia(**m) for m in data_dict.get("media", [])]
        if data_dict.get("video_details"):
            data_dict["video_details"] = VideoDetails(**data_dict["video_details"])

        data_dict["url"] = ResolvedURL(**data_dict["url"])
        scraped_data = ScrapedData(**data_dict)
        return CachedObject(data=scraped_data, expires=obj_dict["expires"])

    class CacheEncoder(json.JSONEncoder):
        """
        Custom JSON encoder to handle serialization of ScrapedData and other
        custom objects. Bytes are not expected here as they are handled before encoding.
        """

        def default(self, o: Any) -> Any:
            if isinstance(
                o, (ScrapedData, CachedObject, VideoDetails, ScrapedMedia, ResolvedURL)
            ):
                return o.__dict__
            if isinstance(o, bytes):
                log.warning("CacheEncoder encountered unexpected bytes data.")
                return None
            return super().default(o)
