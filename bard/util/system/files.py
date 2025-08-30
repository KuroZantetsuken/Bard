import os
import tempfile
from contextlib import asynccontextmanager
from typing import AsyncIterator


@asynccontextmanager
async def create_temp_file(data: bytes, suffix: str) -> AsyncIterator[str]:
    """
    Asynchronously creates a temporary file, writes data to it, and ensures it's cleaned up
    upon exiting the context.

    Args:
        data: The bytes data to write to the temporary file.
        suffix: The file extension (e.g., ".png", ".py", ".ogg").

    Yields:
        The path to the created temporary file.
    """
    temp_path = None
    try:
        with tempfile.NamedTemporaryFile(suffix=suffix, delete=False) as temp_file:
            temp_path = temp_file.name
            temp_file.write(data)
        yield temp_path
    finally:
        if temp_path and os.path.exists(temp_path):
            try:
                os.unlink(temp_path)
            except OSError:
                pass
