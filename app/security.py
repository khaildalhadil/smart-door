"""Passcode hashing and brute-force throttling.

The passcode is never stored or compared in plain text: we keep a
PBKDF2-HMAC-SHA256 digest plus a per-install salt in ``config.ini`` and compare
with :func:`hmac.compare_digest`.

Lockout state is persisted to ``data/state.json`` so power-cycling the Pi does
not hand an attacker a fresh set of attempts.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import os
import threading
import time
from dataclasses import dataclass
from pathlib import Path

from .config import Config, new_salt


def hash_passcode(passcode: str, salt: str, iterations: int) -> str:
    return hashlib.pbkdf2_hmac(
        "sha256", passcode.encode("utf-8"), bytes.fromhex(salt), iterations
    ).hex()


@dataclass
class AttemptResult:
    """Outcome of one passcode submission."""

    ok: bool
    locked_out: bool
    remaining_attempts: int
    lockout_remaining: float
    reason: str = ""


class PasscodeManager:
    """Verifies keypad passcodes and enforces the retry policy."""

    def __init__(self, config: Config, events=None):
        self.config = config
        self.events = events
        self._lock = threading.RLock()
        self._state_path = config.path_for("logging", "file").parent / "state.json"
        self._failures = 0
        self._lockout_until = 0.0
        self._lockout_level = 0
        self._load_state()

    # --------------------------------------------------------------- persistence
    def _load_state(self) -> None:
        try:
            with open(self._state_path, encoding="utf-8") as fh:
                data = json.load(fh)
            self._failures = int(data.get("failures", 0))
            self._lockout_until = float(data.get("lockout_until", 0.0))
            self._lockout_level = int(data.get("lockout_level", 0))
            # A clock that jumped backwards (no RTC on the Pi) must not create
            # an eternal lockout.
            if self._lockout_until - time.time() > self._max_lockout():
                self._lockout_until = time.time() + self._max_lockout()
        except (OSError, ValueError, TypeError):
            self._failures = 0
            self._lockout_until = 0.0
            self._lockout_level = 0

    def _save_state(self) -> None:
        payload = {
            "failures": self._failures,
            "lockout_until": self._lockout_until,
            "lockout_level": self._lockout_level,
        }
        try:
            self._state_path.parent.mkdir(parents=True, exist_ok=True)
            tmp = self._state_path.with_suffix(".json.tmp")
            with open(tmp, "w", encoding="utf-8") as fh:
                json.dump(payload, fh)
            os.replace(tmp, self._state_path)
        except OSError:
            pass

    # ------------------------------------------------------------------ policy
    def _max_attempts(self) -> int:
        return max(1, self.config.getint("security", "max_attempts", 3))

    def _max_lockout(self) -> float:
        return max(1.0, self.config.getfloat("security", "lockout_max_seconds", 600.0))

    def _lockout_duration(self, level: int) -> float:
        base = max(1.0, self.config.getfloat("security", "lockout_seconds", 30.0))
        backoff = max(1.0, self.config.getfloat("security", "lockout_backoff", 2.0))
        return min(base * (backoff ** max(0, level - 1)), self._max_lockout())

    # ------------------------------------------------------------------- state
    @property
    def is_configured(self) -> bool:
        return bool(
            self.config.get("security", "passcode_hash")
            and self.config.get("security", "passcode_salt")
        )

    def lockout_remaining(self) -> float:
        with self._lock:
            return max(0.0, self._lockout_until - time.time())

    def remaining_attempts(self) -> int:
        with self._lock:
            return max(0, self._max_attempts() - self._failures)

    def reset(self) -> None:
        """Clear failures and any active lockout (admin action)."""
        with self._lock:
            self._failures = 0
            self._lockout_until = 0.0
            self._lockout_level = 0
            self._save_state()

    # -------------------------------------------------------------- verifying
    def verify(self, passcode: str) -> AttemptResult:
        with self._lock:
            remaining = self.lockout_remaining()
            if remaining > 0:
                return AttemptResult(False, True, 0, remaining, "locked_out")

            if not self.is_configured:
                return AttemptResult(False, False, self.remaining_attempts(), 0.0,
                                     "not_configured")

            expected = self.config.get("security", "passcode_hash")
            salt = self.config.get("security", "passcode_salt")
            iterations = self.config.getint("security", "pbkdf2_iterations", 200000)
            candidate = hash_passcode(passcode, salt, iterations)

            if hmac.compare_digest(candidate, expected):
                self._failures = 0
                self._lockout_level = 0
                self._lockout_until = 0.0
                self._save_state()
                return AttemptResult(True, False, self._max_attempts(), 0.0, "ok")

            self._failures += 1
            if self._failures >= self._max_attempts():
                self._lockout_level += 1
                self._lockout_until = time.time() + self._lockout_duration(self._lockout_level)
                self._failures = 0
                self._save_state()
                if self.events:
                    self.events.warning(
                        "keypad.lockout",
                        f"too many failures, locked out for "
                        f"{self._lockout_duration(self._lockout_level):.0f}s",
                    )
                return AttemptResult(False, True, 0, self.lockout_remaining(),
                                     "locked_out")

            self._save_state()
            return AttemptResult(False, False, self.remaining_attempts(), 0.0, "wrong")

    # --------------------------------------------------------------- changing
    def validate_new(self, passcode: str) -> str | None:
        """Return an error message, or ``None`` when the passcode is acceptable."""
        lo = self.config.getint("security", "passcode_min_length", 4)
        hi = self.config.getint("security", "passcode_max_length", 12)
        if not passcode.isdigit():
            return "Passcode must contain digits only."
        if not lo <= len(passcode) <= hi:
            return f"Passcode must be {lo}-{hi} digits."
        if len(set(passcode)) == 1:
            return "Passcode must not be a single repeated digit."
        return None

    def set_passcode(self, passcode: str) -> None:
        salt = new_salt()
        iterations = self.config.getint("security", "pbkdf2_iterations", 200000)
        digest = hash_passcode(passcode, salt, iterations)
        self.config.set("security", "passcode_salt", salt)
        self.config.set("security", "passcode_hash", digest)
        self.config.set("security", "pbkdf2_iterations", iterations)
        self.config.save()
        self.reset()
        if self.events:
            self.events.info("passcode.changed", "keypad passcode updated")


class LoginLimiter:
    """Same retry policy as the keypad, applied to user PIN sign-in.

    One shared counter for all accounts: on a door panel the attacker is the
    person at the screen, not a particular account.  Persisted next to the
    keypad state so a reboot does not reset it.
    """

    def __init__(self, config: Config, events=None):
        self.config = config
        self.events = events
        self._lock = threading.Lock()
        self._path = config.path_for("logging", "file").parent / "login_state.json"
        self._failures = 0
        self._until = 0.0
        self._level = 0
        try:
            with open(self._path, encoding="utf-8") as fh:
                data = json.load(fh)
            self._failures = int(data.get("failures", 0))
            self._until = min(float(data.get("until", 0.0)),
                              time.time() + self._max_lockout())
            self._level = int(data.get("level", 0))
        except (OSError, ValueError, TypeError):
            pass

    def _max_lockout(self) -> float:
        return max(1.0, self.config.getfloat("security", "lockout_max_seconds", 600.0))

    def _save(self) -> None:
        try:
            self._path.parent.mkdir(parents=True, exist_ok=True)
            tmp = self._path.with_suffix(".json.tmp")
            with open(tmp, "w", encoding="utf-8") as fh:
                json.dump({"failures": self._failures, "until": self._until,
                           "level": self._level}, fh)
            os.replace(tmp, self._path)
        except OSError:
            pass

    def lockout_remaining(self) -> float:
        with self._lock:
            return max(0.0, self._until - time.time())

    def reset(self) -> None:
        with self._lock:
            self._failures = self._level = 0
            self._until = 0.0
            self._save()

    def record(self, ok: bool, who: str = "") -> AttemptResult:
        max_attempts = max(1, self.config.getint("security", "max_attempts", 3))
        with self._lock:
            if ok:
                self._failures = self._level = 0
                self._until = 0.0
                self._save()
                return AttemptResult(True, False, max_attempts, 0.0, "ok")
            self._failures += 1
            if self._failures < max_attempts:
                self._save()
                return AttemptResult(False, False, max_attempts - self._failures,
                                     0.0, "wrong")
            self._failures = 0
            self._level += 1
            base = max(1.0, self.config.getfloat("security", "lockout_seconds", 30.0))
            backoff = max(1.0, self.config.getfloat("security", "lockout_backoff", 2.0))
            duration = min(base * backoff ** (self._level - 1), self._max_lockout())
            self._until = time.time() + duration
            self._save()
        if self.events:
            self.events.warning("login.lockout",
                                f"too many PIN failures ({who}), locked out {duration:.0f}s")
        return AttemptResult(False, True, 0, duration, "locked_out")
