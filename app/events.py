"""Rotating audit log.

Every unlock, failed attempt, lockout, enrolment and admin action lands here so
the door has a trail.  Entries are also kept in a small in-memory ring buffer so
the Admin > Event Log screen can render without re-reading the file.
"""

from __future__ import annotations

import logging
import threading
from collections import deque
from logging.handlers import RotatingFileHandler

from .config import Config


class EventLog:
    def __init__(self, config: Config, recent: int = 200):
        self.config = config
        self._lock = threading.Lock()
        self._recent: deque[tuple[str, str, str, str]] = deque(maxlen=recent)
        self._listeners: list = []

        path = config.path_for("logging", "file")
        path.parent.mkdir(parents=True, exist_ok=True)

        self._logger = logging.getLogger("smartlock")
        self._logger.setLevel(
            getattr(logging, config.get("logging", "level").upper(), logging.INFO)
        )
        self._logger.propagate = False
        if not self._logger.handlers:
            handler = RotatingFileHandler(
                path,
                maxBytes=config.getint("logging", "max_bytes", 1_048_576),
                backupCount=config.getint("logging", "backup_count", 3),
                encoding="utf-8",
            )
            handler.setFormatter(
                logging.Formatter(
                    "%(asctime)s %(levelname)-7s %(event)-22s %(message)s",
                    datefmt="%Y-%m-%d %H:%M:%S",
                )
            )
            self._logger.addHandler(handler)
        self._load_tail(path, recent)

    def _load_tail(self, path, limit: int) -> None:
        """Seed the ring buffer from the log file so history survives a reboot."""
        try:
            with open(path, encoding="utf-8") as fh:
                lines = deque(fh, maxlen=limit)
        except OSError:
            return
        for line in lines:
            parts = line.rstrip("\n").split(None, 4)
            if len(parts) >= 4:
                date, clock, level, event = parts[:4]
                self._recent.append((f"{date} {clock}", level, event,
                                     parts[4] if len(parts) > 4 else ""))

    # ----------------------------------------------------------------- output
    def _emit(self, level: int, event: str, message: str) -> None:
        import time

        stamp = time.strftime("%Y-%m-%d %H:%M:%S")
        with self._lock:
            self._recent.append((stamp, logging.getLevelName(level), event, message))
            listeners = list(self._listeners)
        self._logger.log(level, message, extra={"event": event})
        for cb in listeners:
            try:
                cb(stamp, logging.getLevelName(level), event, message)
            except Exception:  # a broken UI callback must not break logging
                pass

    def info(self, event: str, message: str = "") -> None:
        self._emit(logging.INFO, event, message)

    def warning(self, event: str, message: str = "") -> None:
        self._emit(logging.WARNING, event, message)

    def error(self, event: str, message: str = "") -> None:
        self._emit(logging.ERROR, event, message)

    # ------------------------------------------------------------------ reads
    def recent(self, limit: int = 100) -> list[tuple[str, str, str, str]]:
        with self._lock:
            items = list(self._recent)
        return items[-limit:][::-1]

    def subscribe(self, callback) -> None:
        with self._lock:
            self._listeners.append(callback)

    def unsubscribe(self, callback) -> None:
        with self._lock:
            if callback in self._listeners:
                self._listeners.remove(callback)
