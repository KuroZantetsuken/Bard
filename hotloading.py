import logging
import os
import re
import signal
import subprocess
import sys
import threading
import time
from typing import Optional

from watchdog.events import FileSystemEventHandler
from watchdog.observers import Observer

from utilities.logging import setup_logging_config

# Initialize logger for the hotloading module.
logger = logging.getLogger("Bard")


class BotRestarter(FileSystemEventHandler):
    """
    Monitors specified directories for file system events and restarts the bot process
    when relevant changes are detected.
    """

    def __init__(self, command: list[str], watch_dirs: list[str]):
        """
        Initializes the BotRestarter.

        Args:
            command: The command to execute the bot (e.g., ["main.py"]).
            watch_dirs: A list of directories to monitor for changes.
        """
        super().__init__()
        self.command = command
        self.watch_dirs = watch_dirs
        self.process: Optional[subprocess.Popen] = None
        self.restart_scheduled = False
        self.restart_timer: Optional[threading.Timer] = None
        self.start_bot()

    def start_bot(self):
        """
        Starts a new bot process or restarts an existing one.
        Terminates any currently running bot process before starting a new one.
        """
        if self.process:
            logger.info("Terminating existing bot process.")
            # Send SIGTERM to the process group to ensure all child processes are terminated.
            os.killpg(os.getpgid(self.process.pid), signal.SIGTERM)
            self.process.wait()  # Wait for the process to terminate.
            logger.info("Bot process terminated.")

        logger.info(
            f"Starting bot with command: {sys.executable} {' '.join(self.command)}"
        )
        # Start the new bot process in a new process group.
        self.process = subprocess.Popen(
            [sys.executable, *self.command], preexec_fn=os.setsid
        )

    def on_any_event(self, event):
        """
        Callback method for any file system event.
        Triggers a bot restart if a watched Python, environment, or prompt file is modified.
        """
        src_path = os.fsdecode(event.src_path)

        # Ignores directory events and files that are not Python, .env, or prompt.md files.
        if event.is_directory or not re.search(r"\.(py|env|prompt\.md)$", src_path):
            return

        # Processes only file modification events (e.g., file saves).
        if event.event_type != "modified":
            return

        # Checks if the modified file is within one of the watched directories.
        if any(src_path.startswith(d) for d in self.watch_dirs):
            logger.info(f"Detected change in {src_path}. Scheduling bot restart.")
            # Cancels any pending restart to debounce multiple rapid changes.
            if self.restart_timer:
                self.restart_timer.cancel()
            # Schedules a restart after a 2.0-second delay.
            self.restart_timer = threading.Timer(2.0, self._perform_restart)
            self.restart_timer.start()

    def _perform_restart(self):
        """
        Executes the bot restart after a debounce period.
        Sets a flag to indicate a restart is in progress.
        """
        self.restart_scheduled = True
        self.start_bot()
        self.restart_scheduled = False


if __name__ == "__main__":
    # Initializes global logging configuration for the application.
    setup_logging_config()
    logger.info("Starting hot-reloader.")

    # Defines the command to run the main bot script.
    bot_command = ["main.py"]
    # Specifies the directories to watch for file changes. Watches the current directory recursively.
    watched_directories = ["./"]

    # Creates an event handler to manage bot restarts.
    event_handler = BotRestarter(bot_command, watched_directories)
    # Initializes an observer to monitor file system events.
    observer = Observer()

    # Schedules the event handler for each watched directory.
    for directory in watched_directories:
        observer.schedule(event_handler, directory, recursive=True)
        logger.info(f"Watching directory: {directory}")

    # Starts the observer thread.
    observer.start()
    logger.info("Hot-reloader active. Press Ctrl+C to stop.")

    try:
        # Keeps the main thread alive to allow the observer to run in the background.
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        # Handles graceful shutdown on Ctrl+C.
        logger.info("Ctrl+C detected. Stopping hot-reloader.")
        observer.stop()  # Stops the observer from monitoring file changes.
        if event_handler.process:
            logger.info("Terminating bot process.")
            # Terminates the bot's process group.
            os.killpg(os.getpgid(event_handler.process.pid), signal.SIGTERM)
            event_handler.process.wait()  # Waits for the bot process to exit.
        logger.info("Hot-reloader stopped and bot process terminated.")
    finally:
        # Ensures the observer thread is properly joined upon exit.
        observer.join()
