import asyncio
import logging
from typing import List, Optional, Tuple

from config import Config

logger = logging.getLogger("Bard")


class FFmpegWrapper:
    """
    A wrapper class for asynchronous execution of FFmpeg commands.
    Provides methods for executing generic FFmpeg commands and specific audio conversions.
    """

    @staticmethod
    async def execute(
        arguments: List[str], input_data: Optional[bytes] = None, timeout: float = 30.0
    ) -> Tuple[Optional[bytes], Optional[bytes], int]:
        """
        Executes an FFmpeg command with specified arguments and optional input data.

        Args:
            arguments: A list of FFmpeg command-line arguments. The first element
                       must be the path to the FFmpeg executable (e.g., 'ffmpeg').
            input_data: Optional bytes to pipe to stdin of the FFmpeg process.
            timeout: The maximum time in seconds to wait for the process to complete.

        Returns:
            A tuple containing:
            - stdout_data (Optional[bytes]): The data captured from stdout.
            - stderr_data (Optional[bytes]): The data captured from stderr.
            - return_code (int): The exit code of the FFmpeg process.
        """
        try:
            process = await asyncio.create_subprocess_exec(
                *arguments,
                stdin=asyncio.subprocess.PIPE,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            try:
                stdout_data, stderr_data = await asyncio.wait_for(
                    process.communicate(input=input_data), timeout=timeout
                )
            except asyncio.TimeoutError:
                logger.debug(f"FFmpeg process timed out after {timeout} seconds.")
                process.kill()
                _, stderr_data_after_kill = await process.communicate()
                error_message = b"Process timed out. " + (stderr_data_after_kill or b"")
                return None, error_message.strip(), -1
            return_code = process.returncode if process.returncode is not None else -1
            return stdout_data, stderr_data, return_code
        except FileNotFoundError:
            logger.critical(
                f"FFmpeg executable not found: '{arguments[0]}'. Ensure it's installed and in PATH."
            )
            return None, b"FFmpeg not found", -1
        except Exception as e:
            logger.critical(f"FFmpeg execution failed: {str(e)}")
            return None, str(e).encode(), -1

    @classmethod
    async def convert_audio(
        cls,
        input_data: bytes,
        input_format: str,
        output_format: str,
        input_args: List[str] = [],
        output_args: List[str] = [],
        timeout: float = 30.0,
    ) -> Optional[bytes]:
        """
        Converts audio data between specified formats using FFmpeg.

        Args:
            input_data: The raw audio data to convert.
            input_format: The format of the input audio (e.g., 's16le', 'f32le').
            output_format: The desired output format (e.g., 'opus', 'mp3').
            input_args: Additional command-line arguments specific to the input.
            output_args: Additional command-line arguments specific to the output.
            timeout: The maximum time in seconds to wait for the conversion to complete.

        Returns:
            The converted audio data in bytes, or None on failure.
        """
        args = [
            Config.FFMPEG_PATH,
            "-y",
            "-f",
            input_format,
            *input_args,
            "-i",
            "-",
            "-f",
            output_format,
            *output_args,
            "-",
        ]
        stdout, stderr, return_code = await cls.execute(args, input_data, timeout)
        if return_code == 0 and stdout:
            return stdout
        error_msg = stderr.decode(errors="ignore") if stderr else "Unknown error"
        logger.error(f"Audio conversion failed with code {return_code}: {error_msg}")
        return None
