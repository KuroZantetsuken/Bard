import json
import logging
import os
from datetime import datetime
from typing import Any

from settings import Settings


def _format_bytes(size_in_bytes: int) -> str:
    """Formats a byte count into a human-readable string."""
    if size_in_bytes < 1024:
        return f"{size_in_bytes}B"
    if size_in_bytes < 1024 * 1024:
        return f"{size_in_bytes / 1024:.2f}KB"
    return f"{size_in_bytes / (1024 * 1024):.2f}MB"


class ConsoleFormatter(logging.Formatter):
    """
    A custom formatter for console output that displays messages in a simple,
    human-readable format.
    """

    def format(self, record: logging.LogRecord) -> str:
        """
        Formats a log record for console output.

        Args:
            record: The log record to format.

        Returns:
            The formatted log message as a string.
        """
        log_fmt = "[%(asctime)s] - %(message)s"
        formatter = logging.Formatter(log_fmt, datefmt="%H:%M")
        return formatter.format(record)


class JsonFormatter(logging.Formatter):
    """
    A custom formatter that outputs log records as a structured JSON string.
    This formatter includes logic for data sanitization and noise reduction.
    """

    RESERVED_ATTRS = {
        "args",
        "asctime",
        "created",
        "exc_info",
        "exc_text",
        "filename",
        "funcName",
        "levelname",
        "levelno",
        "lineno",
        "message",
        "module",
        "msecs",
        "msg",
        "name",
        "pathname",
        "process",
        "processName",
        "relativeCreated",
        "stack_info",
        "thread",
        "threadName",
    }

    TRIM_KEYS = {
        "media",
        "screenshot_data",
        "audio_data",
        "image_data",
        "code_data",
        "waveform_b64",
        "contents",
        "response",
        "data",
    }
    MAX_SIZE = 1024
    MAX_DEPTH = 20

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.log = logging.getLogger(__name__)

    def format(self, record: logging.LogRecord) -> str:
        """
        Formats a log record into a JSON string.

        Args:
            record: The log record to format.

        Returns:
            The formatted log message as a JSON string.
        """
        log_record = {
            "timestamp": datetime.utcfromtimestamp(record.created).isoformat() + "Z",
            "level": record.levelname,
            "message": record.getMessage(),
            "module": record.pathname,
            "function": record.funcName,
            "line": record.lineno,
            "thread_id": record.thread,
        }

        if record.exc_info:
            log_record["exception"] = self.formatException(record.exc_info)

        extra_data = {}
        for key, value in record.__dict__.items():
            if key not in self.RESERVED_ATTRS:
                extra_data[key] = value

        if extra_data:
            log_record["extra"] = self._sanitize_and_trim(extra_data)

        clean_record = self._remove_empty_values(log_record)

        return json.dumps(clean_record, separators=(",", ":"))

    def _sanitize_and_trim(self, data: Any, depth: int = 0) -> Any:
        """
        Recursively sanitizes and trims data to ensure it is JSON serializable
        and does not contain excessively large fields.
        """
        if depth > self.MAX_DEPTH:
            return "<max recursion depth reached>"

        if isinstance(data, dict):
            new_dict = {}
            for key, value in data.items():
                new_key = str(key)
                if (
                    new_key in self.TRIM_KEYS
                    and isinstance(value, (bytes, str))
                    and len(value) > self.MAX_SIZE
                ):
                    new_dict[new_key] = f"<data trimmed: {_format_bytes(len(value))}>"
                else:
                    new_dict[new_key] = self._sanitize_and_trim(value, depth + 1)
            return new_dict
        elif isinstance(data, (list, tuple)):
            return [self._sanitize_and_trim(item, depth + 1) for item in data]
        elif isinstance(data, bytes):
            return f"<bytes data of length: {_format_bytes(len(data))}>"
        elif isinstance(data, (int, float, str, bool)) or data is None:
            return data
        elif hasattr(data, "__dict__"):
            obj_dict = {
                "__class__": data.__class__.__name__,
                **{k: v for k, v in data.__dict__.items() if not k.startswith("_")},
            }
            return self._sanitize_and_trim(obj_dict, depth + 1)
        else:
            try:
                return str(data)
            except Exception:
                return f"<unserializable: {type(data).__name__}>"

    def _remove_empty_values(self, data: Any) -> Any:
        """
        Recursively removes keys with empty or None values from a dictionary.

        Args:
            data: The data structure to clean.

        Returns:
            The cleaned data structure.
        """
        if isinstance(data, dict):
            cleaned_dict = {}
            for k, v in data.items():
                cleaned_v = self._remove_empty_values(v)
                if cleaned_v not in [None, "", [], {}]:
                    cleaned_dict[k] = cleaned_v
            return cleaned_dict
        if isinstance(data, list):
            return [self._remove_empty_values(i) for i in data]
        return data


def _prune_logs():
    """
    Removes old log files based on age and count limits.
    """
    if not Settings.LOG_PRUNE_ON_STARTUP:
        return

    now = datetime.now()
    max_age_days = Settings.LOG_FILE_MAX_AGE_DAYS
    max_count = Settings.LOG_FILE_MAX_COUNT

    try:
        log_files = sorted(
            [
                os.path.join(Settings.LOG_DIR, f)
                for f in os.listdir(Settings.LOG_DIR)
                if f.endswith(".json")
            ],
            key=os.path.getmtime,
            reverse=True,
        )

        if max_count > 0 and len(log_files) > max_count:
            files_to_prune = log_files[max_count:]
            for f in files_to_prune:
                os.remove(f)
            log_files = log_files[:max_count]

        if max_age_days > 0:
            for f in log_files:
                file_age = now - datetime.fromtimestamp(os.path.getmtime(f))
                if file_age.days > max_age_days:
                    os.remove(f)

    except FileNotFoundError:
        pass
    except Exception as e:
        logging.getLogger("Bard").error(f"Error pruning logs: {e}")


def setup_logging():
    """
    Configures the application's logging system with console and file handlers.
    """
    logger = logging.getLogger("Bard")
    if logger.hasHandlers():
        logger.debug("Logging already configured.")
        return logger
    logger.setLevel(logging.DEBUG)
    logger.propagate = False

    if Settings.LOG_CONSOLE_ENABLED:
        console_handler = logging.StreamHandler()
        console_handler.setLevel(Settings.LOG_CONSOLE_LEVEL)
        console_handler.setFormatter(ConsoleFormatter())
        logger.addHandler(console_handler)

    if Settings.LOG_FILE_ENABLED:
        os.makedirs(Settings.LOG_DIR, exist_ok=True)
        _prune_logs()
        log_filename = datetime.now().strftime("%Y-%m-%dT%H-%M-%S.json")
        log_filepath = os.path.join(Settings.LOG_DIR, log_filename)

        file_handler = logging.FileHandler(log_filepath, mode="w", encoding="utf-8")
        file_handler.setLevel(Settings.LOG_FILE_LEVEL)
        file_handler.setFormatter(JsonFormatter())
        logger.addHandler(file_handler)

    logger.info("Logging configured successfully.")
    return logger
