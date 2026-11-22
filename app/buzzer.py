"""Alarm buzzer: beeps while a fire / smoke alarm lasts (app/hazard.py).

The buzzer sits on one GPIO pin (``[buzzer] pin``, GPIO13 by default) and
follows the hazard monitor: it starts on the first sensor in ``alarm`` and
stops once every sensor is quiet again ("cleared").  A "suspect" sensor (wired
wrong) never sounds it.

Unlike the relay, the pin always drives the "off" level at rest.  A wrong
``active_high`` therefore shows at once as a buzzer that sounds from start-up,
instead of a buzzer that stays silent during a real fire.

* Active buzzer (it sounds by itself on DC): ``pwm_hz = 0``.
* Passive buzzer (only clicks on DC): ``pwm_hz`` = a tone, e.g. 2500.
"""

from __future__ import annotations

import threading
import time

from .config import Config


class GpioBuzzer:
    """The buzzer pin through gpiozero (same pin factory as the relay)."""

    def __init__(self, pin: int, active_high: bool, pwm_hz: float):
        if pwm_hz > 0:
            from gpiozero import PWMOutputDevice

            self._device = PWMOutputDevice(pin, active_high=active_high,
                                           initial_value=0, frequency=pwm_hz)
            self._on_value = 0.5  # square wave
        else:
            from gpiozero import DigitalOutputDevice

            self._device = DigitalOutputDevice(pin, active_high=active_high,
                                               initial_value=False)
            self._on_value = 1

    def set(self, on: bool) -> None:
        self._device.value = self._on_value if on else 0

    def close(self) -> None:
        try:
            self._device.value = 0
        finally:
            self._device.close()


class Buzzer:
    def __init__(self, config: Config, events=None, backend=None, clock=time.monotonic):
        self.config = config
        self.events = events
        self.enabled = config.getbool("buzzer", "enabled", False)
        self.pin = config.getint("buzzer", "pin", 13)
        self.active_high = config.getbool("buzzer", "active_high", True)
        self.pwm_hz = max(0.0, config.getfloat("buzzer", "pwm_hz", 0.0))
        self.on_seconds = max(0.05, config.getfloat("buzzer", "on_seconds", 0.5))
        self.off_seconds = max(0.05, config.getfloat("buzzer", "off_seconds", 0.5))
        self.max_seconds = max(0.0, config.getfloat("buzzer", "max_seconds", 300.0))
        self._backend = backend
        self._clock = clock
        self._mutex = threading.Lock()
        self._wanted = False
        self._wake = threading.Event()
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None

    def start(self) -> None:
        if not self.enabled:
            return
        if self._backend is None:
            try:
                self._backend = GpioBuzzer(self.pin, self.active_high, self.pwm_hz)
            except Exception as exc:
                self._log("error", f"cannot drive GPIO{self.pin}: {exc}; buzzer off")
                return
        kind = f"passive, {self.pwm_hz:.0f} Hz" if self.pwm_hz else "active"
        self._log("info", f"ready on GPIO{self.pin} ({kind}, "
                          f"active_{'high' if self.active_high else 'low'})")
        self._thread = threading.Thread(target=self._run, name="buzzer", daemon=True)
        self._thread.start()

    def close(self) -> None:
        self._stop.set()
        self._wake.set()
        if self._thread is not None:
            self._thread.join(timeout=2)
        if self._backend is not None:
            try:
                self._backend.close()
            except Exception:
                pass

    # ---------------------------------------------------------------- control
    def alarm(self, on: bool) -> None:
        """Sound (True) or silence (False).  Returns at once; any thread."""
        with self._mutex:
            if self._wanted == on:
                return
            self._wanted = on
        self._wake.set()

    def on_hazards(self, states: dict) -> None:
        """Listener for :meth:`app.hazard.HazardMonitor.add_listener`."""
        from .hazard import STATE_ALARM

        self.alarm(any(state == STATE_ALARM for state in states.values()))

    # --------------------------------------------------------------- the loop
    def _pause(self, seconds: float | None) -> None:
        """Sleep, but wake early on a change or on close."""
        self._wake.wait(seconds)
        self._wake.clear()

    def _run(self) -> None:
        started = None
        gave_up = False
        while not self._stop.is_set():
            with self._mutex:
                wanted = self._wanted
            if not wanted:
                if started is not None:
                    self._log("info", "silent (alarm over)")
                    started, gave_up = None, False
                self._set(False)
                self._pause(None)
                continue
            if started is None:
                started = self._clock()
                self._log("info", "sounding (alarm)")
            if self.max_seconds and self._clock() - started >= self.max_seconds:
                if not gave_up:
                    gave_up = True
                    self._log("warning", f"silenced after {self.max_seconds:.0f}s; "
                                         f"the alarm itself is still on")
                self._set(False)
                self._pause(None)
                continue
            self._set(True)
            self._pause(self.on_seconds)
            self._set(False)
            if not self._stop.is_set():
                self._pause(self.off_seconds)
        self._set(False)

    def _set(self, on: bool) -> None:
        try:
            self._backend.set(on)
        except Exception as exc:
            self._log("error", f"GPIO{self.pin}: {type(exc).__name__}: {exc}")
            self._stop.wait(1.0)

    def _log(self, level: str, message: str) -> None:
        if self.events:
            getattr(self.events, level)("buzzer", message)
