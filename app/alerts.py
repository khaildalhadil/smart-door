"""Outgoing alerts (fire, smoke, wrong code, doorbell ...) waiting for the
WhatsApp bot.

The bot already reaches this Pi through the reverse SSH tunnel, but the Pi has
no way back to the bot.  So alerts queue here and the bot collects them:

    GET  /alerts               -> {"ok": true, "alerts": [{...}, ...]}
    POST /alerts/ack {"id": 7} -> {"ok": true}   (drops every alert with id <= 7)

An alert stays queued until the bot acknowledges it, so one that arrives while
the bot or the tunnel is down is delivered when they come back (at least once:
the bot may see an alert twice if it dies between sending and acknowledging).
The queue lives in ``data/alerts.json`` (mode 0600) and survives a restart.

The bot sends ``message`` as it is: no AI is involved, so alerts cost no
tokens.  Optional keys:

* ``photo``: ``"/photos/<name>.jpg"`` -- fetch it from this API (same key)
  and send it as an image with ``message`` as the caption;
* ``ask``: true when the owner is expected to answer (the doorbell); the
  answer goes to ``POST /command`` like any other message;
* ``options``: the possible answers, for bots that can show reply buttons.
"""

from __future__ import annotations

import json
import os
import threading
import time

from .config import Config

MAX_PENDING = 50  # oldest ones are dropped past this


class AlertQueue:
    """Thread-safe: the sensor thread adds, the API thread reads and acks."""

    def __init__(self, config: Config, events=None):
        self.config = config
        self.events = events
        self._lock = threading.Lock()
        self._path = config.path_for("logging", "file").parent / "alerts.json"
        self._next_id = 1
        self._pending: list[dict] = []
        self._started = time.monotonic()
        self._last_poll: float | None = None   # monotonic time of the bot's last GET
        self._load()

    # --------------------------------------------------------------- persistence
    def _load(self) -> None:
        try:
            with open(self._path, encoding="utf-8") as fh:
                data = json.load(fh)
            self._next_id = int(data["next_id"])
            self._pending = [dict(a) for a in data.get("pending", []) if isinstance(a, dict)]
        except (OSError, ValueError, TypeError, KeyError):
            self._next_id, self._pending = 1, []

    def _save(self) -> None:
        payload = {"next_id": self._next_id, "pending": self._pending}
        tmp = self._path.with_suffix(".json.tmp")
        try:
            self._path.parent.mkdir(parents=True, exist_ok=True)
            fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
            with os.fdopen(fd, "w", encoding="utf-8") as fh:
                json.dump(payload, fh, ensure_ascii=False, indent=1)
            os.replace(tmp, self._path)
        except OSError as exc:
            if self.events:
                self.events.error("alert.save", f"cannot write {self._path.name}: {exc}")

    # -------------------------------------------------------------------- API
    def add(self, kind: str, event: str, message: str, door_opened: bool = False,
            photo: str = "", ask: bool = False, options: list[dict] | None = None) -> int:
        """Queue one alert; returns its id.

        ``photo`` is a file name in data/photos (see app/history.py).
        """
        with self._lock:
            alert_id = self._next_id
            self._next_id += 1
            alert = {
                "id": alert_id,
                "type": kind,            # "fire", "smoke", "keypad", "doorbell", ...
                "event": event,          # "detected", "cleared", "denied", "ring", ...
                "message": message,      # ready to send as the WhatsApp text
                "door_opened": bool(door_opened),
                "time": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
            }
            if photo:
                alert["photo"] = f"/photos/{photo}"
            if ask:
                alert["ask"] = True
            if options:
                alert["options"] = options
            self._pending.append(alert)
            dropped = len(self._pending) - MAX_PENDING
            if dropped > 0:
                del self._pending[:dropped]
            self._save()
        if self.events:
            self.events.info("alert.queued", f"#{alert_id} {kind} {event}")
            if dropped > 0:
                self.events.warning("alert.dropped",
                                    f"{dropped} old alert(s) never collected by the bot")
        return alert_id

    def pending(self) -> list[dict]:
        with self._lock:
            return [dict(a) for a in self._pending]

    # ------------------------------------------------------------ bot presence
    def polled(self) -> None:
        """The bot just asked for the alerts: it is alive and can reach us."""
        with self._lock:
            self._last_poll = time.monotonic()

    def poll_age(self) -> float | None:
        """Seconds since the bot last collected alerts; None if it never has
        since this app started."""
        with self._lock:
            if self._last_poll is None:
                return None
            return time.monotonic() - self._last_poll

    def bot_online(self, grace: float | None = None) -> bool:
        """False once the bot has been silent for ``[bot] offline_after_seconds``.

        Just after start-up it gets that long to show up before it counts as
        offline.
        """
        if grace is None:
            grace = max(5.0, self.config.getfloat("bot", "offline_after_seconds", 45.0))
        age = self.poll_age()
        if age is None:
            return time.monotonic() - self._started < grace
        return age < grace

    def ack(self, up_to: int) -> int:
        """Forget every alert with ``id <= up_to``; returns how many went."""
        with self._lock:
            before = len(self._pending)
            self._pending = [a for a in self._pending if a["id"] > up_to]
            gone = before - len(self._pending)
            if gone:
                self._save()
        if gone and self.events:
            self.events.info("alert.delivered", f"bot took {gone} alert(s) up to #{up_to}")
        return gone
