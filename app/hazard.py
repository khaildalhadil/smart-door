"""Fire and smoke sensors: open the door and alert the WhatsApp bot.

Each sensor is a module with a digital output (DO) on one GPIO pin: an IR
flame module ([fire]) and later an MQ-2 style smoke/gas module ([smoke]).  One
thread polls both pins ten times a second.

When a sensor confirms (active at least half the time for ``confirm_seconds``):

* the door opens for ``lock.max_unlock_seconds`` (``[alarm] open_door``), and
  opens again every time it has been shut for ``reopen_after_seconds`` while
  the alarm lasts, at most ``max_reopens`` times;
* an alert is queued for the bot (:mod:`app.alerts`), and another one when the
  sensor has been quiet for ``clear_seconds``;
* the status bar and the door screen show the alarm.

Safety against a mis-wired sensor: a sensor only arms after it has read
"no fire" for ``ARM_SECONDS``.  One that reads "fire" from the moment the app
starts (wrong ``active_low``, wrong pin) never opens the door: it raises a
"suspect" alert instead, so the owner checks it.
"""

from __future__ import annotations

import threading
import time
from collections import deque
from dataclasses import dataclass, field

from .config import Config

KINDS = ("fire", "smoke")
POLL_SECONDS = 0.1
ARM_SECONDS = 2.0     # quiet this long before a sensor can trigger
STUCK_SECONDS = 20.0  # still not quiet after this long -> "suspect" alert
ACTIVE_RATIO = 0.5    # share of samples that must be active in the window

# Sensor states, as reported to listeners.
STATE_WARMUP = "warmup"    # smoke heater still warming; readings ignored
STATE_ARMING = "arming"    # waiting to read "no fire" for ARM_SECONDS
STATE_OK = "ok"
STATE_ALARM = "alarm"
STATE_SUSPECT = "suspect"  # active ever since start: wiring / polarity?

LABELS = {"fire": "Fire", "smoke": "Smoke"}

# WhatsApp texts (Arabic, the owner's language).  {time} is HH:MM.
MESSAGES = {
    ("fire", "detected"): "🔥 تنبيه حريق! حساس النار اكتشف لهب عند الباب.",
    ("fire", "cleared"): "✅ انتهى تنبيه الحريق: حساس النار لم يعد يرى لهب.",
    ("fire", "suspect"): ("⚠️ حساس النار يعطي إشارة «نار» باستمرار منذ تشغيل النظام. "
                          "غالبًا التوصيل أو الإعداد خطأ، فلم يُفتح الباب. افحص الحساس."),
    ("smoke", "detected"): "💨 تنبيه دخان! حساس الدخان اكتشف دخان أو غاز.",
    ("smoke", "cleared"): "✅ انتهى تنبيه الدخان: الحساس رجع طبيعي.",
    ("smoke", "suspect"): ("⚠️ حساس الدخان يعطي إشارة «دخان» باستمرار منذ تشغيل النظام. "
                           "غالبًا التوصيل أو الإعداد خطأ، فلم يُفتح الباب. افحص الحساس."),
}
DOOR_OPENED = "🚪 تم فتح الباب تلقائيًا."
DOOR_NOT_OPENED = "🚪 لم يُفتح الباب (عطل في الريلاي)."


def alert_text(kind: str, event: str, door: str | None = None) -> str:
    """``door``: "opened", "failed" or None (say nothing about the door)."""
    lines = [MESSAGES[(kind, event)]]
    if door == "opened":
        lines.append(DOOR_OPENED)
    elif door == "failed":
        lines.append(DOOR_NOT_OPENED)
    lines.append("الساعة " + time.strftime("%H:%M"))
    return "\n".join(lines)


# -------------------------------------------------------------------- pin input
class GpioInput:
    """One input pin through gpiozero's pin factory (same one the relay uses)."""

    def __init__(self, pin: int, pull: str):
        from gpiozero import Device

        Device.ensure_pin_factory()
        self._pin = Device.pin_factory.pin(pin)
        self._pin.input_with_pull(pull)

    def read(self) -> int:
        return 1 if self._pin.state else 0

    def close(self) -> None:
        self._pin.close()


@dataclass
class SensorSpec:
    kind: str
    pin: int
    active_low: bool
    pull: str
    confirm_seconds: float
    clear_seconds: float
    warmup_seconds: float

    @classmethod
    def from_config(cls, config: Config, kind: str) -> "SensorSpec":
        active_low = config.getbool(kind, "active_low", True)
        pull = config.get(kind, "pull").strip().lower() or "auto"
        if pull == "auto":
            # Unplugged or unpowered must read "no fire".
            pull = "up" if active_low else "down"
        if pull in ("off", "none", "floating"):
            pull = "floating"
        return cls(
            kind=kind,
            pin=config.getint(kind, "pin", 0),
            active_low=active_low,
            pull=pull,
            confirm_seconds=max(0.0, config.getfloat(kind, "confirm_seconds", 1.0)),
            clear_seconds=max(1.0, config.getfloat(kind, "clear_seconds", 15.0)),
            warmup_seconds=max(0.0, config.getfloat(kind, "warmup_seconds", 0.0)),
        )


@dataclass
class _Sensor:
    spec: SensorSpec
    reader: object
    started: float
    state: str = STATE_ARMING
    quiet_since: float | None = None
    last_active: float = 0.0
    samples: deque = field(default_factory=deque)
    window_start: float = 0.0


# ---------------------------------------------------------------------- monitor
class HazardMonitor:
    def __init__(self, config: Config, lock, alerts, events=None, reader_factory=None,
                 clock=time.monotonic):
        self.config = config
        self.lock = lock
        self.alerts = alerts
        self.events = events
        self._clock = clock
        self._make_reader = reader_factory or GpioInput
        self._mutex = threading.Lock()
        self._listeners: list = []
        self._sensors: list[_Sensor] = []
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None

        self.open_door = config.getbool("alarm", "open_door", True)
        self.reopen_after = max(0.0, config.getfloat("alarm", "reopen_after_seconds", 20.0))
        self.max_reopens = max(0, config.getint("alarm", "max_reopens", 15))
        self._door_shut_since: float | None = None
        self._reopens = 0
        self._reopen_limit_logged = False

    # ------------------------------------------------------------------ set-up
    def start(self) -> None:
        now = self._clock()
        for kind in KINDS:
            if not self.config.getbool(kind, "enabled", False):
                continue
            spec = SensorSpec.from_config(self.config, kind)
            if spec.pin <= 0:
                self._log("error", f"hazard.{kind}", "enabled but no pin set; ignored")
                continue
            try:
                reader = self._make_reader(spec.pin, spec.pull)
            except Exception as exc:
                self._log("error", f"hazard.{kind}",
                          f"cannot read GPIO{spec.pin}: {exc}; sensor ignored")
                continue
            sensor = _Sensor(spec=spec, reader=reader, started=now,
                             state=STATE_WARMUP if spec.warmup_seconds > 0 else STATE_ARMING)
            self._sensors.append(sensor)
            self._log("info", f"hazard.{kind}",
                      f"watching GPIO{spec.pin} (active_{'low' if spec.active_low else 'high'}, "
                      f"pull {spec.pull})"
                      + (f", warming up {spec.warmup_seconds:.0f}s" if spec.warmup_seconds else ""))
        if self._sensors:
            self._thread = threading.Thread(target=self._run, name="hazard", daemon=True)
            self._thread.start()

    def close(self) -> None:
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout=2)
        for sensor in self._sensors:
            try:
                sensor.reader.close()
            except Exception:
                pass

    # -------------------------------------------------------------- observation
    def add_listener(self, callback) -> None:
        """``callback(states: dict kind -> state)`` whenever a state changes."""
        with self._mutex:
            self._listeners.append(callback)

    def states(self) -> dict[str, str]:
        with self._mutex:
            return {s.spec.kind: s.state for s in self._sensors}

    def alarms(self) -> list[str]:
        return [kind for kind, state in self.states().items() if state == STATE_ALARM]

    # ---------------------------------------------------------------- the loop
    def _run(self) -> None:
        while not self._stop.wait(POLL_SECONDS):
            try:
                self.poll()
            except Exception as exc:  # never let one bad read end the watch
                self._log("error", "hazard.exception", f"{type(exc).__name__}: {exc}")
                self._stop.wait(1.0)

    def poll(self) -> None:
        """One pass over every sensor, then the door.  Public for tests."""
        now = self._clock()
        changed = False
        for sensor in self._sensors:
            level = sensor.reader.read()
            active = (level == 0) if sensor.spec.active_low else (level == 1)
            changed |= self._step(sensor, active, now)
        self._door(now)
        if changed:
            states = self.states()
            with self._mutex:
                listeners = list(self._listeners)
            for cb in listeners:
                try:
                    cb(states)
                except Exception:
                    pass

    def _set(self, sensor: _Sensor, state: str) -> bool:
        with self._mutex:
            if sensor.state == state:
                return False
            sensor.state = state
        return True

    def _step(self, sensor: _Sensor, active: bool, now: float) -> bool:
        spec, kind = sensor.spec, sensor.spec.kind
        if active:
            sensor.last_active = now
            sensor.quiet_since = None
        elif sensor.quiet_since is None:
            sensor.quiet_since = now
        quiet_for = 0.0 if sensor.quiet_since is None else now - sensor.quiet_since

        if sensor.state == STATE_WARMUP:
            if now - sensor.started < spec.warmup_seconds:
                return False
            sensor.started = now  # the stuck timer starts after the warm-up
            sensor.quiet_since = None if active else now
            return self._set(sensor, STATE_ARMING)

        if sensor.state in (STATE_ARMING, STATE_SUSPECT):
            if quiet_for >= ARM_SECONDS:
                if sensor.state == STATE_SUSPECT:
                    self._log("info", f"hazard.{kind}", "sensor reads normal now; armed")
                else:
                    self._log("info", f"hazard.{kind}", "armed")
                sensor.samples.clear()
                sensor.window_start = now
                return self._set(sensor, STATE_OK)
            if sensor.state == STATE_ARMING and now - sensor.started >= STUCK_SECONDS:
                self._log("error", f"hazard.{kind}",
                          f"GPIO{spec.pin} has read '{kind}' ever since start -- wrong pin "
                          f"or wrong active_low?  Door NOT opened")
                self.alerts.add(kind, "suspect", alert_text(kind, "suspect"))
                return self._set(sensor, STATE_SUSPECT)
            return False

        if sensor.state == STATE_OK:
            window = spec.confirm_seconds
            sensor.samples.append((now, active))
            while sensor.samples and sensor.samples[0][0] < now - window:
                sensor.samples.popleft()
            if not active:
                return False
            if window > 0:
                if now - sensor.window_start < window:
                    return False  # not a full window of samples yet
                hits = sum(1 for _t, a in sensor.samples if a)
                if hits < ACTIVE_RATIO * len(sensor.samples):
                    return False
            self._trigger(sensor, now)
            return self._set(sensor, STATE_ALARM)

        # STATE_ALARM
        if now - sensor.last_active >= spec.clear_seconds:
            self._log("info", f"hazard.{kind}",
                      f"{kind} alarm over (quiet for {spec.clear_seconds:.0f}s)")
            self.alerts.add(kind, "cleared", alert_text(kind, "cleared"))
            sensor.samples.clear()
            sensor.window_start = now
            return self._set(sensor, STATE_OK)
        return False

    # -------------------------------------------------------------------- door
    def _unlock(self, reason: str) -> bool:
        seconds = self.config.getfloat("lock", "max_unlock_seconds", 10.0)
        return self.lock.unlock(seconds, reason=reason)

    def _trigger(self, sensor: _Sensor, now: float) -> None:
        kind = sensor.spec.kind
        self._log("warning", f"hazard.{kind}", f"{kind.upper()} DETECTED on GPIO{sensor.spec.pin}")
        door = None
        if self.open_door:
            door = "opened" if self._unlock(f"{kind} alarm") else "failed"
            if not self.alarms():  # first alarm: start the re-open budget
                self._reopens = 0
                self._reopen_limit_logged = False
            self._door_shut_since = None
        self.alerts.add(kind, "detected", alert_text(kind, "detected", door),
                        door_opened=door == "opened")

    def _door(self, now: float) -> None:
        """While an alarm lasts, open the door again after it has been shut
        ``reopen_after`` seconds (the solenoid gets that long to cool)."""
        alarms = self.alarms()
        if not alarms or not self.open_door or self.reopen_after <= 0:
            self._door_shut_since = None
            return
        if self.lock.status().is_unlocked:
            self._door_shut_since = None
            return
        if self._door_shut_since is None:
            self._door_shut_since = now
            return
        if now - self._door_shut_since < self.reopen_after:
            return
        if self._reopens >= self.max_reopens:
            if not self._reopen_limit_logged:
                self._reopen_limit_logged = True
                self._log("warning", "hazard.door",
                          f"re-opened {self._reopens} times; leaving the door locked "
                          f"until the alarm clears")
            return
        self._reopens += 1
        self._door_shut_since = None
        self._unlock(" + ".join(alarms) + f" alarm, re-open {self._reopens}")

    # --------------------------------------------------------------------- log
    def _log(self, level: str, event: str, message: str) -> None:
        if self.events:
            getattr(self.events, level)(event, message)
