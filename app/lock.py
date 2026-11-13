"""Relay / solenoid control.

The solenoid is never driven from a GPIO pin directly: the pin drives a relay
module, the relay switches the 12 V supply through COM -> NO, and the lock's
negative returns to the shared supply negative.  See WIRING.md.

Safety properties implemented here:

* **Released at rest.**  The pin is driven only while the door should be open.
  On startup, after every unlock and on shutdown (unhandled exception and
  SIGTERM included) it is released: an input with no pull, the same as an idle
  Pi pin.  The relay is then off whichever level the board switches on with,
  so a wrong ``relay_active_high`` fails as "the door does not open", never as
  "the lock stays powered".
* **Hard duration ceiling.**  ``lock.max_unlock_seconds`` clamps every request,
  so a bad config or a UI bug cannot cook the solenoid coil.
* **Watchdog.**  A single supervisor thread re-asserts the expected output level
  every ``lock.watchdog_interval`` seconds and force-locks anything that has
  outlived its deadline.  A lost timer cannot leave the door open.
* **Idempotent re-entry.**  A second unlock request while already unlocked
  extends the deadline rather than stacking timers.
"""

from __future__ import annotations

import enum
import threading
import time
from dataclasses import dataclass

from .config import Config


class LockState(enum.Enum):
    LOCKED = "locked"
    UNLOCKED = "unlocked"


@dataclass
class LockStatus:
    """Immutable snapshot handed to UI observers."""

    state: LockState
    seconds_remaining: float
    backend: str
    simulated: bool
    error: str = ""

    @property
    def is_unlocked(self) -> bool:
        return self.state is LockState.UNLOCKED


# --------------------------------------------------------------------- backends
class RelayBackend:
    """Interface for anything that can energise the relay coil."""

    name = "none"
    simulated = True

    def set_active(self, active: bool) -> None:  # pragma: no cover - interface
        raise NotImplementedError

    def close(self) -> None:
        pass


class SimulationBackend(RelayBackend):
    """Used on a development machine, or when no GPIO library is usable."""

    name = "simulation"
    simulated = True

    def __init__(self, reason: str = ""):
        self.reason = reason
        self.active = False

    def set_active(self, active: bool) -> None:
        self.active = active


class GpiozeroBackend(RelayBackend):
    """Preferred backend, using gpiozero's pin API to release the pin at rest.

    ``DigitalOutputDevice`` cannot do that: it always drives an "off" level.
    Releasing also switches 5 V active-low boards fully off, where a 3.3 V HIGH
    can leave them half on.
    """

    name = "gpiozero"
    simulated = False

    def __init__(self, pin: int, active_high: bool):
        from gpiozero import Device

        Device.ensure_pin_factory()
        self._pin = Device.pin_factory.pin(pin)
        self._on_level = 1 if active_high else 0
        self._pin.input_with_pull("floating")

    def set_active(self, active: bool) -> None:
        pin = self._pin
        if active:
            if pin.function != "output" or pin.state != self._on_level:
                pin.output_with_state(self._on_level)
        elif pin.function != "input":
            pin.input_with_pull("floating")

    def close(self) -> None:
        try:
            self._pin.input_with_pull("floating")
        finally:
            self._pin.close()


class RPiGPIOBackend(RelayBackend):
    """Fallback for installs where gpiozero's pin factory fails; also releases
    the pin at rest."""

    name = "RPi.GPIO"
    simulated = False

    def __init__(self, pin: int, active_high: bool):
        import RPi.GPIO as GPIO

        self._gpio = GPIO
        self._pin = pin
        self._on_level = GPIO.HIGH if active_high else GPIO.LOW
        GPIO.setwarnings(False)
        GPIO.setmode(GPIO.BCM)
        self.set_active(False)

    def set_active(self, active: bool) -> None:
        GPIO = self._gpio
        if active:
            GPIO.setup(self._pin, GPIO.OUT, initial=self._on_level)
        else:
            GPIO.setup(self._pin, GPIO.IN, pull_up_down=GPIO.PUD_OFF)

    def close(self) -> None:
        try:
            self.set_active(False)
        finally:
            self._gpio.cleanup(self._pin)


def create_backend(config: Config, events=None) -> RelayBackend:
    """Pick the best available backend, degrading to simulation."""
    pin = config.getint("lock", "relay_pin", 17)
    active_high = config.getbool("lock", "relay_active_high", True)
    errors: list[str] = []

    for factory in (GpiozeroBackend, RPiGPIOBackend):
        try:
            backend = factory(pin, active_high)
        except Exception as exc:  # missing lib, no /dev/gpiochip, wrong platform
            errors.append(f"{factory.name}: {exc}")
            continue
        if events:
            events.info(
                "lock.backend",
                f"{backend.name} on BCM{pin} (active_high={active_high})",
            )
        return backend

    reason = "; ".join(errors) or "no GPIO library available"
    if events:
        events.warning("lock.backend", f"simulation mode -- {reason}")
    return SimulationBackend(reason)


# ------------------------------------------------------------------ controller
class LockController:
    """Owns the lock state and keeps the hardware and the UI in agreement."""

    def __init__(self, config: Config, events=None, backend: RelayBackend | None = None):
        self.config = config
        self.events = events
        self.backend = backend if backend is not None else create_backend(config, events)

        self._lock = threading.RLock()
        self._wake = threading.Condition(self._lock)
        self._state = LockState.LOCKED
        self._deadline = 0.0
        self._error = ""
        self._closed = False
        self._observers: list = []
        self._last_notified: tuple | None = None

        # Drive the safe level before anything else can ask for an unlock.
        self._apply(False)

        self._thread = threading.Thread(
            target=self._supervise, name="lock-supervisor", daemon=True
        )
        self._thread.start()

    # ------------------------------------------------------------- observation
    def add_observer(self, callback) -> None:
        """``callback(LockStatus)``; may be called from the supervisor thread."""
        with self._lock:
            self._observers.append(callback)
        callback(self.status())

    def remove_observer(self, callback) -> None:
        with self._lock:
            if callback in self._observers:
                self._observers.remove(callback)

    def status(self) -> LockStatus:
        with self._lock:
            remaining = max(0.0, self._deadline - time.monotonic())
            return LockStatus(
                state=self._state,
                seconds_remaining=remaining if self._state is LockState.UNLOCKED else 0.0,
                backend=self.backend.name,
                simulated=self.backend.simulated,
                error=self._error,
            )

    def _status_changed(self) -> bool:
        """True when the snapshot differs from the one last pushed to observers.

        Keeps the 1 Hz watchdog tick from redrawing an idle screen forever.
        """
        status = self.status()
        key = (
            status.state,
            int(status.seconds_remaining * 10),
            status.error,
        )
        if key == self._last_notified:
            return False
        return True

    def _notify(self) -> None:
        status = self.status()
        with self._lock:
            self._last_notified = (
                status.state,
                int(status.seconds_remaining * 10),
                status.error,
            )
            observers = list(self._observers)
        for cb in observers:
            try:
                cb(status)
            except Exception:
                pass

    # ----------------------------------------------------------------- output
    def _apply(self, active: bool) -> bool:
        """Drive the relay; record and report any hardware failure."""
        try:
            self.backend.set_active(active)
            if self._error:
                self._error = ""
            return True
        except Exception as exc:
            self._error = str(exc)
            if self.events:
                self.events.error("lock.io", f"relay write failed: {exc}")
            return False

    # ---------------------------------------------------------------- actions
    def unlock(self, seconds: float | None = None, reason: str = "") -> bool:
        """Energise the relay for ``seconds`` (clamped), then auto-lock."""
        duration = self.config.unlock_seconds if seconds is None else float(seconds)
        ceiling = self.config.getfloat("lock", "max_unlock_seconds", 10.0)
        duration = max(0.2, min(duration, ceiling))

        with self._lock:
            if self._closed:
                return False
            ok = self._apply(True)
            if not ok:
                self._state = LockState.LOCKED
                self._deadline = 0.0
                self._wake.notify_all()
                self._notify()
                return False
            self._state = LockState.UNLOCKED
            # Extend rather than stack: one deadline, one supervisor.
            self._deadline = max(self._deadline, time.monotonic() + duration)
            self._wake.notify_all()

        if self.events:
            self.events.info(
                "lock.unlock", f"{duration:.1f}s" + (f" ({reason})" if reason else "")
            )
        self._notify()
        return True

    def lock(self, reason: str = "") -> None:
        """Return to the fail-secure state immediately."""
        with self._lock:
            was_unlocked = self._state is LockState.UNLOCKED
            self._apply(False)
            self._state = LockState.LOCKED
            self._deadline = 0.0
            self._wake.notify_all()
        if was_unlocked and self.events:
            self.events.info("lock.lock", reason or "relocked")
        self._notify()

    def pulse_test(self, seconds: float = 1.0) -> bool:
        """Short relay click used by the Admin polarity self-test."""
        if self.events:
            self.events.warning("lock.test", f"relay self-test {seconds:.1f}s")
        return self.unlock(seconds, reason="self-test")

    # -------------------------------------------------------------- supervisor
    def _supervise(self) -> None:
        interval = self.config.getfloat("lock", "watchdog_interval", 1.0)
        while True:
            with self._lock:
                if self._closed:
                    return
                if self._state is LockState.UNLOCKED:
                    remaining = self._deadline - time.monotonic()
                    timeout = remaining if remaining > 0 else 0.0
                    if interval > 0:
                        timeout = min(timeout, interval) if timeout > 0 else 0.0
                else:
                    timeout = interval if interval > 0 else None

                if timeout is None or timeout > 0:
                    self._wake.wait(timeout)
                    if self._closed:
                        return

                expired = (
                    self._state is LockState.UNLOCKED
                    and time.monotonic() >= self._deadline
                )
                if expired:
                    self._apply(False)
                    self._state = LockState.LOCKED
                    self._deadline = 0.0
                else:
                    # Watchdog: re-assert the level the state machine expects, so a
                    # glitched or externally-changed pin is corrected within one tick.
                    self._apply(self._state is LockState.UNLOCKED)

            if expired:
                if self.events:
                    self.events.info("lock.lock", "auto-locked (timer expired)")
                self._notify()
            elif self._status_changed():
                # Tick the UI only when the visible snapshot actually moved.
                self._notify()

    # ------------------------------------------------------------------ teardown
    def close(self) -> None:
        """Always safe to call twice; leaves the door locked."""
        with self._lock:
            if self._closed:
                return
            self._closed = True
            self._state = LockState.LOCKED
            self._deadline = 0.0
            self._wake.notify_all()
        try:
            self.backend.set_active(False)
        except Exception:
            pass
        try:
            self.backend.close()
        except Exception:
            pass
        if self.events:
            self.events.info("lock.shutdown", "relay released, door locked")

    def __enter__(self) -> "LockController":
        return self

    def __exit__(self, *exc) -> None:
        self.close()
