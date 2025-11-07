import os
import tempfile
from contextlib import asynccontextmanager
from typing import AsyncIterator


class TemporaryFile:
    def __init__(self, data: bytes, suffix: str):
        self.data = data
        self.suffix = suffix
        self.temp_path = None

    async def __aenter__(self) -> str:
        """
        Asynchronously creates a temporary file and writes data to it.

        Returns:
            The path to the created temporary file.
        """
        with tempfile.NamedTemporaryFile(
            suffix=self.suffix, delete=False
        ) as temp_file:
            self.temp_path = temp_file.name
            temp_file.write(self.data)
        return self.temp_path

    async def __aexit__(self, exc_type, exc_val, exc_tb):
        """
        Cleans up the temporary file upon exiting the context.
        """
        if self.temp_path and os.path.exists(self.temp_path):
            try:
                os.unlink(self.temp_path)
            except OSError:
                pass
