import json
import logging
import os
from datetime import datetime, timedelta
from typing import Any


class LogSanitizer:
    @staticmethod
    def clean_dict(d: Any) -> Any:
        """
        Recursively removes keys with None values from a dictionary or list of dictionaries.
        """
        if isinstance(d, dict):
            return {
                k: LogSanitizer.clean_dict(v) for k, v in d.items() if v is not None
            }
        if isinstance(d, list):
            return [LogSanitizer.clean_dict(i) for i in d if i is not None]
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

    if max_age_days > 0:
        cutoff_time = datetime.now() - timedelta(days=max_age_days)
        for filename in os.listdir(log_dir):
            if filename.endswith(".log"):
                filepath = os.path.join(log_dir, filename)
                try:
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

    if max_count > 0:
        log_files = sorted(
            [
                os.path.join(log_dir, f)
                for f in os.listdir(log_dir)
                if f.endswith(".log")
            ],
            key=os.path.getmtime,
        )

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

    return str(obj)


class LogFormatter:
    @staticmethod
    def prettify_json(data: Any) -> str:
        """
        Formats a JSON-like data structure into a human-readable, indented string for logging.
        It also sanitizes the data before pretty-printing.
        It now handles google.genai.types objects by converting them to dictionaries.
        """
        try:
            if hasattr(data, "model_dump"):
                data = data.model_dump()
            elif hasattr(data, "to_dict"):
                data = data.to_dict()

            if isinstance(data, str):
                try:
                    data = json.loads(data)
                except json.JSONDecodeError:
                    return str(data)

            sanitized_data = sanitize_response_for_logging(data)

            return json.dumps(sanitized_data, indent=2, default=json_serializer)
        except (json.JSONDecodeError, TypeError):
            return str(data)


class LoggingConfigurator:
    def __init__(self):
        from config import Config

        self.config = Config

    def setup(self):
        """
        Configures the application's logging system based on settings in `config.py`.
        It initializes console and file handlers conditionally, applies filters,
        and prunes old log files at startup.
        """
        if self.config.LOG_PRUNE_ON_STARTUP:
            prune_old_logs(
                self.config.LOG_DIR,
                self.config.LOG_FILE_MAX_AGE_DAYS,
                self.config.LOG_FILE_MAX_COUNT,
            )

        for handler in logging.root.handlers[:]:
            logging.root.removeHandler(handler)

        logger = logging.getLogger("Bard")

        logger.setLevel(logging.DEBUG)
        logger.propagate = False

        if self.config.LOG_CONSOLE_ENABLED:
            console_handler = logging.StreamHandler()
            console_handler.setLevel(self.config.LOG_CONSOLE_LEVEL)
            console_formatter = logging.Formatter(
                "%(asctime)s %(levelname)-8s %(message)s", "%H:%M:%S"
            )
            console_handler.setFormatter(console_formatter)
            console_handler.addFilter(JsonPayloadFilter())
            logger.addHandler(console_handler)

        if self.config.LOG_FILE_ENABLED:
            os.makedirs(self.config.LOG_DIR, exist_ok=True)
            log_filename = datetime.now().strftime("%Y-%m-%dT%H:%M:%S.log")
            log_filepath = os.path.join(self.config.LOG_DIR, log_filename)
            file_handler = logging.FileHandler(
                log_filepath, mode="w", encoding="utf-8"
            )
            file_handler.setLevel(self.config.LOG_FILE_LEVEL)
            detailed_formatter = logging.Formatter(
                "%(asctime)s [%(levelname)-5s] [%(name)s:%(module)s:%(funcName)s:%(lineno)d] %(message)s"
            )
            file_handler.setFormatter(detailed_formatter)

            logger.addHandler(file_handler)

        log_message = "Logging configured."
        logger.info(log_message)
        return logger
