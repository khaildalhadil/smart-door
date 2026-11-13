"""One-time guest codes, issued remotely through ``POST /tempCode``.

A guest code opens the door once from the keypad and is deleted the moment it
is used; it is also deleted when its time runs out.  Like the door passcode,
codes are stored only as PBKDF2 digests, in ``data/temp_codes.json`` (mode
0600), so they survive an app restart.

All codes share one salt, so a keypad entry is hashed once however many codes
are active, and not at all when there are none.
"""

from __future__ import annotations

import hmac
import json
import os
import secrets
import threading
import time

from .config import Config, new_salt
from .security import hash_passcode

MIN_DIGITS, MAX_DIGITS = 4, 8
MIN_SECONDS, MAX_SECONDS = 10, 24 * 3600


def normalize_code(code) -> str | None:
    """Return the code as ASCII digits, or ``None`` if it is not 4-8 digits.

    Arabic-Indic digits (as typed on an Arabic phone keyboard) are accepted
    and converted, since the keypad only ever enters 0-9.
    """
    if not isinstance(code, str) or not code.isdecimal():
        return None
    code = "".join(str(int(ch)) for ch in code)
    return code if MIN_DIGITS <= len(code) <= MAX_DIGITS else None


def valid_seconds(seconds) -> bool:
    return (isinstance(seconds, int) and not isinstance(seconds, bool)
            and MIN_SECONDS <= seconds <= MAX_SECONDS)


class TempCodeStore:
    """Thread-safe: the API adds codes, the keypad matches and consumes them."""

    def __init__(self, config: Config, events=None):
        self.config = config
        self.events = events
        self._lock = threading.RLock()
        self._path = config.path_for("logging", "file").parent / "temp_codes.json"
        self._salt = ""
        self._iterations = config.getint("security", "pbkdf2_iterations", 200000)
        self._codes: list[dict] = []  # {"id", "hash", "expires"} -- epoch seconds
        self._load()
        self._stop = threading.Event()
        self._thread = threading.Thread(target=self._sweep, name="temp-codes", daemon=True)
        self._thread.start()

    # --------------------------------------------------------------- persistence
    def _load(self) -> None:
        try:
            with open(self._path, encoding="utf-8") as fh:
                data = json.load(fh)
            self._salt = str(data["salt"])
            self._iterations = int(data["iterations"])
            self._codes = [
                {"id": str(c["id"]), "hash": str(c["hash"]), "expires": float(c["expires"])}
                for c in data.get("codes", [])
            ]
        except (OSError, ValueError, TypeError, KeyError):
            self._codes = []
        if not self._salt:
            self._salt = new_salt()
        with self._lock:
            self._purge()

    def _save(self) -> None:
        payload = {"salt": self._salt, "iterations": self._iterations, "codes": self._codes}
        try:
            self._path.parent.mkdir(parents=True, exist_ok=True)
            tmp = self._path.with_suffix(".json.tmp")
            fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
            with os.fdopen(fd, "w", encoding="utf-8") as fh:
                json.dump(payload, fh)
            os.replace(tmp, self._path)
        except OSError as exc:
            if self.events:
                self.events.error("tempcode.io", f"could not save guest codes: {exc}")

    # ------------------------------------------------------------------ expiry
    @staticmethod
    def _live(entry: dict, now: float) -> bool:
        # The upper bound discards entries made unbelievable by a clock jump
        # (the Pi has no RTC), so nothing outlives the 24 h maximum.
        return 0 < entry["expires"] - now <= MAX_SECONDS

    def _purge(self) -> None:
        """Delete expired codes.  Caller holds ``self._lock``."""
        now = time.time()
        expired = [c for c in self._codes if not self._live(c, now)]
        if not expired:
            return
        self._codes = [c for c in self._codes if self._live(c, now)]
        self._save()
        if self.events:
            for c in expired:
                self.events.info("tempcode.expired", f"#{c['id']}")

    def _sweep(self) -> None:
        while not self._stop.wait(1.0):
            with self._lock:
                self._purge()

    # ----------------------------------------------------------------- actions
    def add(self, code, seconds) -> int:
        """Store a guest code valid for ``seconds``; return that duration.

        Raises ``ValueError`` (message safe to return to the client) for a bad
        code or duration, or when too many codes are already active.  Adding
        a code that is already active replaces it with the new duration.
        """
        normal = normalize_code(code)
        if normal is None:
            raise ValueError(f"code must be {MIN_DIGITS}-{MAX_DIGITS} digits, as a string")
        if not valid_seconds(seconds):
            raise ValueError(f"seconds must be an integer from {MIN_SECONDS} to {MAX_SECONDS}")
        digest = hash_passcode(normal, self._salt, self._iterations)
        limit = max(1, self.config.getint("api", "max_temp_codes", 20))

        with self._lock:
            self._purge()
            replaced = [c for c in self._codes if hmac.compare_digest(c["hash"], digest)]
            others = [c for c in self._codes if c not in replaced]
            if len(others) >= limit:
                raise ValueError(f"too many active codes (limit {limit})")
            taken = {c["id"] for c in others}
            code_id = secrets.token_hex(2)
            while code_id in taken:
                code_id = secrets.token_hex(2)
            entry = {"id": code_id, "hash": digest, "expires": time.time() + seconds}
            self._codes = others + [entry]
            self._save()
        if self.events:
            note = f" (replaces #{replaced[0]['id']})" if replaced else ""
            self.events.info("tempcode.add", f"#{entry['id']} valid {seconds}s{note}")
        return seconds

    def match(self, code: str) -> str | None:
        """Id of the live code equal to ``code``, or ``None``.  Does not consume."""
        with self._lock:
            self._purge()
            if not self._codes:
                return None
            salt, iterations = self._salt, self._iterations
        normal = normalize_code(code)
        if normal is None:
            return None
        digest = hash_passcode(normal, salt, iterations)
        now = time.time()
        with self._lock:
            found = None
            for c in self._codes:
                if hmac.compare_digest(c["hash"], digest) and self._live(c, now):
                    found = c["id"]
            return found

    def consume(self, code_id: str) -> bool:
        """Delete the code; ``True`` only if it was still live (single use)."""
        with self._lock:
            now = time.time()
            entry = next((c for c in self._codes if c["id"] == code_id), None)
            if entry is None or not self._live(entry, now):
                self._purge()
                return False
            self._codes.remove(entry)
            self._save()
        if self.events:
            self.events.info("tempcode.used", f"#{code_id}")
        return True

    def active_count(self) -> int:
        with self._lock:
            self._purge()
            return len(self._codes)

    def close(self) -> None:
        self._stop.set()
