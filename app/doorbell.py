"""The doorbell: a button on the door screen that asks the owner on WhatsApp.

    visitor presses "Ring bell"
        -> ``doorbell.ring`` in the event log
        -> app/recorder.py takes a picture and queues the alert
           "someone is at the door -- open?" for the bot
    owner answers "yes" / "no" on WhatsApp
        -> the bot forwards the text to POST /command (app/commands.py)
        -> :meth:`Doorbell.answer` opens the door, or not
        -> the door screen tells the visitor

An answer only counts for ``[doorbell] answer_seconds`` after the ring.  Past
that a bare "yes" opens nothing (the visitor has probably left); the owner can
still send an explicit "open the door".
"""

from __future__ import annotations

import threading
import time

from .config import Config

# States reported to listeners (the door screen).
RINGING = "ringing"
OPENED = "opened"        # the owner said yes and the relay switched
DECLINED = "declined"    # the owner said no
TIMEOUT = "timeout"      # nobody answered in time
FAULT = "fault"          # the owner said yes but the relay failed


class Doorbell:
    """Thread-safe: the Tk thread rings, an API thread answers."""

    def __init__(self, config: Config, events, lock, clock=time.monotonic):
        self.config = config
        self.events = events
        self.lock = lock
        self._clock = clock
        self._mutex = threading.Lock()
        self._listeners: list = []
        self._ring_id = 0
        self._last_ring: float | None = None
        self._deadline = 0.0
        self._timer: threading.Timer | None = None
        self.enabled = config.getbool("doorbell", "enabled", True)
        self.answer_seconds = max(10.0, config.getfloat("doorbell", "answer_seconds", 120.0))
        self.min_gap = max(0.0, config.getfloat("doorbell", "min_gap_seconds", 20.0))

    def add_listener(self, callback) -> None:
        """``callback(state)`` on every change, from whichever thread caused it."""
        with self._mutex:
            self._listeners.append(callback)

    def _emit(self, state: str) -> None:
        with self._mutex:
            listeners = list(self._listeners)
        for cb in listeners:
            try:
                cb(state)
            except Exception:  # a broken screen callback must not break the bell
                pass

    # ------------------------------------------------------------------- state
    def pending(self) -> float:
        """Seconds left to answer the last ring; 0 when nothing is waiting."""
        with self._mutex:
            return max(0.0, self._deadline - self._clock())

    def retry_in(self) -> float:
        """Seconds until the button accepts another ring."""
        with self._mutex:
            if self._last_ring is None:
                return 0.0
            return max(0.0, self._last_ring + self.min_gap - self._clock())

    # ----------------------------------------------------------------- actions
    def ring(self) -> bool:
        """The visitor pressed the button.  False if it was pressed again too
        soon (see :meth:`retry_in`) or the bell is switched off."""
        if not self.enabled:
            return False
        with self._mutex:
            now = self._clock()
            if self._last_ring is not None and now - self._last_ring < self.min_gap:
                return False
            again = self._deadline > now
            self._last_ring = now
            self._ring_id += 1
            self._deadline = now + self.answer_seconds
            if self._timer is not None:
                self._timer.cancel()
            self._timer = threading.Timer(self.answer_seconds, self._expire,
                                          args=(self._ring_id,))
            self._timer.daemon = True
            self._timer.start()
        self.events.info("doorbell.ring",
                         "bell pressed again, still no answer" if again
                         else "bell pressed on the door screen")
        self._emit(RINGING)
        return True

    def answer(self, open_door: bool, who: str = "") -> str:
        """The owner answered.  Returns OPENED, DECLINED, FAULT, or TIMEOUT
        when no ring was waiting any more (nothing is done then)."""
        with self._mutex:
            if self._deadline <= self._clock():
                return TIMEOUT
            self._deadline = 0.0
            if self._timer is not None:
                self._timer.cancel()
                self._timer = None
        by = f" ({who})" if who else ""
        if not open_door:
            self.events.info("doorbell.answer", f"owner declined{by}")
            self._emit(DECLINED)
            return DECLINED
        if not self.lock.unlock(reason="whatsapp doorbell"):
            self.events.error("doorbell.answer", f"owner said open{by}, relay fault")
            self._emit(FAULT)
            return FAULT
        self.events.info("doorbell.answer", f"owner opened the door{by}")
        self._emit(OPENED)
        return OPENED

    def _expire(self, ring_id: int) -> None:
        with self._mutex:
            if ring_id != self._ring_id or self._deadline == 0.0:
                return  # answered, or rung again, meanwhile
            self._deadline = 0.0
            self._timer = None
        self.events.info("doorbell.timeout",
                         f"no answer within {self.answer_seconds:.0f}s")
        self._emit(TIMEOUT)

    def close(self) -> None:
        with self._mutex:
            if self._timer is not None:
                self._timer.cancel()
                self._timer = None
            self._deadline = 0.0
