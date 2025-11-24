import asyncio
import functools
import logging
from typing import Any, Callable, Coroutine, Type, TypeVar

from typing_extensions import ParamSpec

logger = logging.getLogger(__name__)

DEFAULT_RETRIES = 3
DEFAULT_DELAY = 1
DEFAULT_BACKOFF = 2


T = TypeVar("T")
P = ParamSpec("P")


def async_retry(
    retries: int = DEFAULT_RETRIES,
    delay: int = DEFAULT_DELAY,
    backoff: int = DEFAULT_BACKOFF,
    retry_on: tuple[Type[Exception], ...] = (Exception,),
) -> Callable[
    [Callable[P, Coroutine[Any, Any, T]]], Callable[P, Coroutine[Any, Any, T]]
]:
    """
    A decorator to automatically retry an async function.

    Args:
        retries: The maximum number of retries.
        delay: The initial delay between retries in seconds.
        backoff: The multiplier for the delay for each subsequent retry.
        retry_on: A tuple of exception types to catch and trigger a retry.
    """

    def decorator(
        func: Callable[P, Coroutine[Any, Any, T]],
    ) -> Callable[P, Coroutine[Any, Any, T]]:
        @functools.wraps(func)
        async def wrapper(*args: P.args, **kwargs: P.kwargs) -> T:
            current_delay = delay
            for i in range(retries):
                try:
                    return await func(*args, **kwargs)
                except retry_on as e:
                    if i == retries - 1:
                        logger.error(
                            "Function %s failed after %d retries.",
                            func.__name__,
                            retries,
                        )
                        raise
                    logger.warning(
                        "Function %s failed with %s. Retrying in %d seconds...",
                        func.__name__,
                        e,
                        current_delay,
                    )
                    await asyncio.sleep(current_delay)
                    current_delay *= backoff

            # Should not be reached because of the raise in the loop,
            # but satisfying the type checker.
            raise RuntimeError("Unreachable")

        return wrapper

    return decorator
