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

log = logging.getLogger("Bard")


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
            log.info("Terminating existing bot process.")

            os.killpg(os.getpgid(self.process.pid), signal.SIGTERM)
            self.process.wait()
            log.info("Bot process terminated.")

        log.info(
            f"Starting bot with command: {sys.executable} {' '.join(self.command)}"
        )

        env = os.environ.copy()
        src_path = os.path.dirname(os.path.abspath(__file__))
        env["PYTHONPATH"] = src_path + os.pathsep + env.get("PYTHONPATH", "")

        self.process = subprocess.Popen(
            [sys.executable, *self.command], preexec_fn=os.setsid, env=env
        )

    def on_any_event(self, event):
        """
        Callback method for any file system event.
        Triggers a bot restart if a watched Python, environment, or prompt file is modified.
        """
        src_path = os.fsdecode(event.src_path)

        if event.is_directory or not re.search(r"\.(py|env|prompt\.md)$", src_path):
            return

        if event.event_type != "modified":
            return

        if any(src_path.startswith(d) for d in self.watch_dirs):
            log.info(f"Detected change in {src_path}. Scheduling bot restart.")

            if self.restart_timer:
                self.restart_timer.cancel()

            self.restart_timer = threading.Timer(5.0, self._perform_restart)
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
    log.info("Starting hot-reloader.")

    bot_command = ["src/main.py"]

    watched_directories = ["."]

    event_handler = BotRestarter(bot_command, watched_directories)

    observer = Observer()

    for directory in watched_directories:
        observer.schedule(event_handler, directory, recursive=True)
        log.info(f"Watching directory: {directory}")

    observer.start()
    log.info("Hot-reloader active. Press Ctrl+C to stop.")

    try:
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        log.info("Ctrl+C detected. Stopping hot-reloader.")
        observer.stop()
        if event_handler.process:
            log.info("Terminating bot process.")

            os.killpg(os.getpgid(event_handler.process.pid), signal.SIGTERM)
            event_handler.process.wait()
        log.info("Hot-reloader stopped and bot process terminated.")
    finally:
        observer.join()
