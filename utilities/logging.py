import json
import logging
import os
from datetime import datetime, timedelta
from typing import Any


def clean_dict(d: Any) -> Any:
    """
    Recursively removes keys with None values from a dictionary or list of dictionaries.
    """
    if isinstance(d, dict):
        return {k: clean_dict(v) for k, v in d.items() if v is not None}
    if isinstance(d, list):
        return [clean_dict(i) for i in d if i is not None]
    return d


class JsonPayloadFilter(logging.Filter):
    """
    A logging filter that prevents log records containing specific JSON API payloads
    from being displayed in the console. This helps keep console output clean.
    """

    def filter(self, record):
        """
        Filters log records.

        Args:
            record: The log record to filter.

        Returns:
            True if the record should be processed, False otherwise.
        """
        msg = record.getMessage()
        return not (msg.startswith("REQUEST to") or msg.startswith("RESPONSE from"))


def prune_old_logs(log_dir: str, max_age_days: int, max_count: int):
    """
    Prunes old log files from the specified directory based on age and count.

    Args:
        log_dir: The directory where log files are stored.
        max_age_days: The maximum age in days for log files before they are pruned (0 to disable).
        max_count: The maximum number of log files to keep (0 to disable pruning by count).
    """
    logger = logging.getLogger("Bard")

    # Prune by age.
    if max_age_days > 0:
        cutoff_time = datetime.now() - timedelta(days=max_age_days)
        for filename in os.listdir(log_dir):
            if filename.endswith(".log"):
                filepath = os.path.join(log_dir, filename)
                try:
                    # Assumes log filenames are in the format YYYY-MM-DDTHH:MM:SS.log.
                    file_timestamp_str = filename.split(".")[0]
                    file_timestamp = datetime.strptime(
                        file_timestamp_str, "%Y-%m-%dT%H:%M:%S"
                    )
                    if file_timestamp < cutoff_time:
                        os.remove(filepath)
                except ValueError:
                    logger.debug(
                        f"Could not parse timestamp from log filename: {filename}. Skipping age pruning for this file."
                    )
                except Exception as e:
                    logger.error(f"Error pruning log file {filename} by age: {e}")

    # Prune by count.
    if max_count > 0:
        log_files = sorted(
            [
                os.path.join(log_dir, f)
                for f in os.listdir(log_dir)
                if f.endswith(".log")
            ],
            key=os.path.getmtime,
        )
        # Adjust pruning to account for the new log file created at startup.
        # This ensures that after a new log is created, the total count is exactly max_count.
        if len(log_files) >= max_count:
            files_to_prune = log_files[: len(log_files) - (max_count - 1)]
            for f in files_to_prune:
                try:
                    os.remove(f)
                except Exception as e:
                    logger.error(
                        f"Error pruning log file {os.path.basename(f)} by count: {e}"
                    )


def sanitize_response_for_logging(data: Any) -> Any:
    """
    Recursively sanitizes an API response payload for logging by redacting sensitive
    or large binary data.

    Args:
        data: The data structure (dict, list, str, bytes, datetime, None) to sanitize.

    Returns:
        The sanitized data structure.
    """
    if data is None:
        return None
    if isinstance(data, datetime):
        return data.isoformat()
    if isinstance(data, bytes):
        return f"<... {len(data)} bytes of binary data ...>"
    if isinstance(data, dict):
        return {
            key: sanitize_response_for_logging(value) for key, value in data.items()
        }
    if isinstance(data, list):
        return [sanitize_response_for_logging(item) for item in data]
    return data


def json_serializer(obj: Any) -> str:
    """
    Custom JSON serializer for objects not serializable by default `json` module.
    Handles `bytes` objects by replacing them with a placeholder and converts other
    unserilizable objects to their string representation.

    Args:
        obj: The object to serialize.

    Returns:
        A JSON serializable representation of the object.
    """
    if isinstance(obj, bytes):
        return "<raw_data_omitted>"
    # For enums and other objects, convert to string.
    return str(obj)


def prettify_json_for_logging(data: Any) -> str:
    """
    Formats a JSON-like data structure into a human-readable, indented string for logging.
    It also sanitizes the data before pretty-printing.

    Args:
        data: The JSON-like data (can be a string or a Python object).

    Returns:
        A pretty-printed and sanitized JSON string, or a string representation of the data
        if JSON parsing fails.
    """
    try:
        if isinstance(data, str):
            data = json.loads(data)

        # Sanitize the data before pretty-printing.
        sanitized_data = sanitize_response_for_logging(data)

        return json.dumps(sanitized_data, indent=2, default=json_serializer)
    except (json.JSONDecodeError, TypeError):
        # Fallback in case of unexpected errors.
        return str(data)


def setup_logging_config():
    """
    Configures the application's logging system based on settings in `config.py`.
    It initializes console and file handlers conditionally, applies filters,
    and prunes old log files at startup.
    """
    from config import Config

    # Prune old logs at startup if enabled.
    if Config.LOG_PRUNE_ON_STARTUP:
        prune_old_logs(
            Config.LOG_DIR, Config.LOG_FILE_MAX_AGE_DAYS, Config.LOG_FILE_MAX_COUNT
        )

    # Clear existing handlers on the root logger to prevent duplicate logging.
    for handler in logging.root.handlers[:]:
        logging.root.removeHandler(handler)

    # Set up the main "Bard" logger.
    logger = logging.getLogger("Bard")
    # Set to DEBUG to allow all messages to pass to handlers.
    logger.setLevel(logging.DEBUG)
    logger.propagate = False

    # Conditionally create and configure StreamHandler (for console logging).
    if Config.LOG_CONSOLE_ENABLED:
        console_handler = logging.StreamHandler()
        console_handler.setLevel(Config.LOG_CONSOLE_LEVEL)
        console_formatter = logging.Formatter(
            "%(asctime)s %(levelname)-8s %(message)s", "%H:%M:%S"
        )
        console_handler.setFormatter(console_formatter)
        console_handler.addFilter(JsonPayloadFilter())
        logger.addHandler(console_handler)

    # Conditionally create and configure FileHandler (for file logging).
    if Config.LOG_FILE_ENABLED:
        os.makedirs(Config.LOG_DIR, exist_ok=True)
        log_filename = datetime.now().strftime("%Y-%m-%dT%H:%M:%S.log")
        log_filepath = os.path.join(Config.LOG_DIR, log_filename)
        file_handler = logging.FileHandler(log_filepath, mode="w", encoding="utf-8")
        file_handler.setLevel(Config.LOG_FILE_LEVEL)
        detailed_formatter = logging.Formatter(
            "%(asctime)s [%(levelname)-5s] [%(name)s:%(module)s:%(funcName)s:%(lineno)d] %(message)s"
        )
        file_handler.setFormatter(detailed_formatter)
        # JsonPayloadFilter is NOT added to file_handler.
        logger.addHandler(file_handler)

    # Remove all emojis from log messages within this function.
    log_message = "Logging configured."
    logger.info(log_message)
    return logger
