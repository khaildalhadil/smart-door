"""Fingerprint service: enrolment, deletion, listing and identification.

Three backends behind one interface:

``disabled``   -- the default.  Nothing opens the serial port.  Every call
                  returns a message explaining exactly what is missing, because
                  the R22's pinout and protocol are not confirmed yet.
``simulated``  -- a fake sensor so the whole admin flow (enrol / list / delete /
                  identify) can be exercised with no hardware attached.
``hardware``   -- the real module via :mod:`app.r22_driver`, unlocked only once
                  ``fingerprint.enabled`` **and** ``fingerprint.probe_confirmed``
                  are both true.

Sensor I/O takes seconds, so every operation runs on a single worker thread and
reports back through callbacks.  The Tk layer wraps those callbacks in
``App.post`` so no widget is ever touched off the main thread.
"""

from __future__ import annotations

import json
import os
import random
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path

from .config import Config
from .r22_driver import R22Sensor, SensorError, SensorTimeout

MODE_DISABLED = "disabled"
MODE_SIMULATED = "simulated"
MODE_HARDWARE = "hardware"


class NotConfigured(SensorError):
    """Raised when a sensor operation is attempted with no usable backend."""


class Cancelled(SensorError):
    """The person stopped the scan or enrolment."""


@dataclass
class OpResult:
    ok: bool
    message: str = ""
    data: dict = field(default_factory=dict)


# --------------------------------------------------------------------- backends
class FingerprintBackend:
    mode = MODE_DISABLED
    #: True when the home screen may watch the sensor for a touch.
    supports_watch = False

    def open(self) -> None:
        raise NotConfigured(self.unavailable_reason())

    def close(self) -> None:
        pass

    def unavailable_reason(self) -> str:
        return "Fingerprint sensor is not configured."

    # Operations -- all raise through open() on the disabled backend.
    def info(self) -> dict:
        raise NotConfigured(self.unavailable_reason())

    def index_table(self) -> set[int]:
        raise NotConfigured(self.unavailable_reason())

    def template_count(self) -> int:
        raise NotConfigured(self.unavailable_reason())

    def enroll(self, slot: int, progress, cancelled) -> None:
        raise NotConfigured(self.unavailable_reason())

    def delete(self, slot: int) -> None:
        raise NotConfigured(self.unavailable_reason())

    def clear(self) -> None:
        raise NotConfigured(self.unavailable_reason())

    def identify(self, progress, cancelled) -> tuple[int, int]:
        raise NotConfigured(self.unavailable_reason())

    def poll_finger(self) -> bool:
        raise NotConfigured(self.unavailable_reason())

    def match_image(self) -> tuple[int, int]:
        raise NotConfigured(self.unavailable_reason())


class DisabledBackend(FingerprintBackend):
    mode = MODE_DISABLED

    def __init__(self, reason: str):
        self._reason = reason

    def unavailable_reason(self) -> str:
        return self._reason


class SimulatedBackend(FingerprintBackend):
    """Fake sensor for bench testing on a machine with no module attached."""

    mode = MODE_SIMULATED

    def __init__(self, config: Config, store_path: Path):
        self.config = config
        self._path = store_path
        self._slots: set[int] = set()
        self._load()

    def _load(self) -> None:
        try:
            with open(self._path, encoding="utf-8") as fh:
                self._slots = {int(s) for s in json.load(fh).get("slots", [])}
        except (OSError, ValueError, TypeError):
            self._slots = set()

    def _save(self) -> None:
        try:
            self._path.parent.mkdir(parents=True, exist_ok=True)
            tmp = self._path.with_suffix(".json.tmp")
            with open(tmp, "w", encoding="utf-8") as fh:
                json.dump({"slots": sorted(self._slots)}, fh)
            os.replace(tmp, self._path)
        except OSError:
            pass

    def open(self) -> None:
        return

    def info(self) -> dict:
        return {
            "system_id": 0,
            "capacity": self.config.getint("fingerprint", "capacity", 127),
            "security_level": 3,
            "baudrate": self.config.getint("fingerprint", "baudrate", 57600),
            "simulated": True,
        }

    def index_table(self) -> set[int]:
        return set(self._slots)

    def template_count(self) -> int:
        return len(self._slots)

    def enroll(self, slot: int, progress, cancelled) -> None:
        for step, text in (
            (1, "Place your finger on the sensor"),
            (2, "Lift your finger"),
            (3, "Place the same finger again"),
            (4, "Saving template"),
        ):
            if cancelled():
                raise Cancelled("Enrolment cancelled.")
            progress(step, 4, text)
            time.sleep(0.8)
        self._slots.add(int(slot))
        self._save()

    def delete(self, slot: int) -> None:
        time.sleep(0.2)
        self._slots.discard(int(slot))
        self._save()

    def clear(self) -> None:
        time.sleep(0.3)
        self._slots.clear()
        self._save()

    def identify(self, progress, cancelled) -> tuple[int, int]:
        progress(1, 2, "Place your finger on the sensor")
        time.sleep(1.0)
        if cancelled():
            raise Cancelled("Scan cancelled.")
        progress(2, 2, "Matching")
        time.sleep(0.5)
        if not self._slots:
            raise SensorError("No fingerprint matched.", 0x09)
        # Deterministic enough to be useful, random enough to exercise failure.
        if random.random() < 0.15:
            raise SensorError("No fingerprint matched.", 0x09)
        return sorted(self._slots)[0], 120


class HardwareBackend(FingerprintBackend):
    """The real R22 module."""

    mode = MODE_HARDWARE
    supports_watch = True

    def __init__(self, config: Config):
        self.config = config
        self._sensor = R22Sensor(
            port=config.get("fingerprint", "port"),
            baudrate=config.getint("fingerprint", "baudrate", 57600),
            address=config.getint("fingerprint", "address", 0xFFFFFFFF),
            password=config.getint("fingerprint", "password", 0),
            timeout=config.getfloat("fingerprint", "timeout", 2.0),
        )
        self._opened = False

    # ------------------------------------------------------------- connection
    def open(self) -> None:
        if self._opened and self._sensor.is_open:
            return
        self._sensor.open()
        self._sensor.verify_password()
        self._sensor.read_parameters()
        self._opened = True

    def close(self) -> None:
        self._opened = False
        self._sensor.close()

    def _capacity(self) -> int:
        reported = self._sensor.parameters.get("capacity")
        configured = self.config.getint("fingerprint", "capacity", 127)
        return int(reported or configured)

    # ------------------------------------------------------------- operations
    def info(self) -> dict:
        self.open()
        params = dict(self._sensor.read_parameters())
        params["simulated"] = False
        params["port"] = self._sensor.port
        return params

    def index_table(self) -> set[int]:
        self.open()
        return self._sensor.index_table(self._capacity())

    def template_count(self) -> int:
        self.open()
        return self._sensor.template_count()

    def enroll(self, slot: int, progress, cancelled) -> None:
        """Two-scan enrolment, exactly as the module expects it."""
        self.open()
        try:
            progress(1, 4, "Place your finger on the sensor")
            self._sensor.capture(timeout=15.0)
            self._sensor.image_to_buffer(1)

            progress(2, 4, "Lift your finger")
            self._sensor.wait_for_finger_removed(timeout=10.0)
            if cancelled():
                raise Cancelled("Enrolment cancelled.")

            progress(3, 4, "Place the same finger again")
            self._sensor.capture(timeout=15.0)
            self._sensor.image_to_buffer(2)

            progress(4, 4, "Saving template")
            self._sensor.build_model()
            self._sensor.store(slot, 1)
        except Exception:
            self._sensor.cancel()
            raise

    def delete(self, slot: int) -> None:
        self.open()
        self._sensor.delete(slot)

    def clear(self) -> None:
        self.open()
        self._sensor.clear_library()

    def identify(self, progress, cancelled) -> tuple[int, int]:
        self.open()
        progress(1, 2, "Place your finger on the sensor")
        self._sensor.capture(timeout=10.0)
        if cancelled():
            self._sensor.cancel()
            raise Cancelled("Scan cancelled.")
        self._sensor.image_to_buffer(1)
        progress(2, 2, "Matching")
        return self._sensor.search(1, 0, self._capacity())

    def poll_finger(self) -> bool:
        """One capture attempt; True when a finger image is now on the module."""
        self.open()
        return self._sensor.try_capture()

    def match_image(self) -> tuple[int, int]:
        """Search the library for the image :meth:`poll_finger` just took."""
        self._sensor.image_to_buffer(1)
        return self._sensor.search(1, 0, self._capacity())


def create_backend(config: Config, events=None) -> FingerprintBackend:
    """Resolve the configured backend, honouring the probe interlock."""
    data_dir = config.path_for("logging", "file").parent

    if config.getbool("fingerprint", "simulate", False):
        if events:
            events.info("fingerprint.backend", "simulated sensor")
        return SimulatedBackend(config, data_dir / "sim_fingerprints.json")

    if not config.getbool("fingerprint", "enabled", False):
        return DisabledBackend(
            "Fingerprint sensor is disabled.\n\n"
            "The R22 module's pinout and protocol are not confirmed yet. "
            "Identify VCC / TX / RX / GND, wire it to the Pi UART, then run:\n"
            "    python3 tools/probe_fingerprint.py\n\n"
            "Set [fingerprint] simulate = true in config.ini to try these "
            "screens without hardware."
        )

    if not config.getbool("fingerprint", "probe_confirmed", False):
        return DisabledBackend(
            "Fingerprint sensor is enabled but not yet verified.\n\n"
            "Run the read-only probe first so we know the module really speaks "
            "this protocol:\n"
            "    python3 tools/probe_fingerprint.py\n\n"
            "It sets [fingerprint] probe_confirmed = true on success."
        )

    try:
        backend = HardwareBackend(config)
    except Exception as exc:
        if events:
            events.error("fingerprint.backend", f"hardware init failed: {exc}")
        return DisabledBackend(f"Could not initialise the sensor:\n{exc}")

    if events:
        events.info(
            "fingerprint.backend",
            f"hardware on {config.get('fingerprint', 'port')} @ "
            f"{config.getint('fingerprint', 'baudrate', 57600)}",
        )
    return backend


# ---------------------------------------------------------------------- service
@dataclass
class _Watch:
    token: int
    on_finger: object = None
    on_result: object = None
    #: False after a scan until the finger has left the sensor.
    armed: bool = True


class FingerprintService:
    """Serialises sensor access: one operation at a time, plus an optional
    background watch that scans whatever finger touches the sensor."""

    def __init__(self, config: Config, users=None, events=None, backend=None):
        self.config = config
        self.users = users
        self.events = events
        self.backend = backend if backend is not None else create_backend(config, events)
        self._lock = threading.Lock()
        self._busy = False
        self._cancel = threading.Event()
        self._thread: threading.Thread | None = None
        # One transaction on the wire at a time: operations hold it for their
        # whole run, the watch for one poll (or one poll + match).
        self._sensor_lock = threading.Lock()
        self._watch: _Watch | None = None
        self._watch_token = 0
        self._watch_thread: threading.Thread | None = None
        self._stop = threading.Event()
        self._online: bool | None = None
        self._listeners: list = []

    # -------------------------------------------------------------- properties
    @property
    def mode(self) -> str:
        return self.backend.mode

    @property
    def available(self) -> bool:
        return self.backend.mode != MODE_DISABLED

    def status_text(self) -> str:
        if self.backend.mode == MODE_DISABLED:
            return self.backend.unavailable_reason()
        if self.backend.mode == MODE_SIMULATED:
            return "Simulated sensor (no hardware attached)."
        return (
            f"Hardware sensor on {self.config.get('fingerprint', 'port')} @ "
            f"{self.config.getint('fingerprint', 'baudrate', 57600)} baud."
        )

    @property
    def auto_scan(self) -> bool:
        """Touching the sensor is enough -- the home screen needs no button."""
        return (self.backend.supports_watch
                and self.config.getbool("fingerprint", "auto_scan", True))

    @property
    def online(self) -> bool | None:
        """Last seen state of the module: None until it has been talked to."""
        return self._online

    def add_listener(self, callback) -> None:
        """``callback(online: bool)`` whenever the module stops or starts answering."""
        with self._lock:
            self._listeners.append(callback)

    def _set_online(self, online: bool) -> None:
        with self._lock:
            before, self._online = self._online, online
            listeners = list(self._listeners) if before != online else []
        if before == online:
            return
        if self.events:
            if not online:
                self.events.warning("fingerprint.offline",
                                    "sensor not responding (power? TX/RX wiring?)")
            elif before is False:
                self.events.info("fingerprint.online", "sensor responding again")
        for callback in listeners:
            try:
                callback(online)
            except Exception:
                pass

    def is_busy(self) -> bool:
        with self._lock:
            return self._busy

    def cancel(self) -> None:
        self._cancel.set()

    # ----------------------------------------------------------------- runner
    def _run(self, name: str, work, progress, done) -> bool:
        """Start ``work(progress, cancelled)`` on the worker thread."""
        with self._lock:
            if self._busy:
                if done:
                    done(OpResult(False, "The sensor is busy with another operation."))
                return False
            self._busy = True
            self._cancel.clear()

        def target() -> None:
            result: OpResult
            try:
                with self._sensor_lock:
                    payload = work(progress or (lambda *a: None), self._cancel.is_set)
                result = payload if isinstance(payload, OpResult) else OpResult(
                    True, "", payload or {}
                )
                if self.backend.mode == MODE_HARDWARE:
                    self._set_online(True)
            except NotConfigured as exc:
                result = OpResult(False, str(exc))
            except Cancelled as exc:
                result = OpResult(False, str(exc), {"cancelled": True})
            except SensorTimeout as exc:
                result = OpResult(False, f"Sensor timeout -- {exc}",
                                  {"code": exc.code, "timeout": True})
                # 0x02 is "nobody put a finger down" -- the module did answer.
                self._set_online(exc.code == 0x02)
            except SensorError as exc:
                result = OpResult(False, str(exc), {"code": exc.code})
                if exc.code is not None:
                    self._set_online(True)
            except Exception as exc:  # pragma: no cover - unexpected
                result = OpResult(False, f"Unexpected sensor error: {exc}")
                if self.events:
                    self.events.error("fingerprint.error", f"{name}: {exc}")
            finally:
                with self._lock:
                    self._busy = False
            if done:
                done(result)

        self._thread = threading.Thread(target=target, name=f"fp-{name}", daemon=True)
        self._thread.start()
        return True

    # ------------------------------------------------------------- operations
    def connect_async(self, done=None) -> bool:
        def work(progress, cancelled):
            progress(1, 1, "Connecting to sensor")
            info = self.backend.info()
            count = self.backend.template_count()
            if self.events:
                self.events.info(
                    "fingerprint.connect",
                    f"capacity={info.get('capacity')} stored={count}",
                )
            return OpResult(True, "Sensor online.", {"info": info, "count": count})

        return self._run("connect", work, None, done)

    def index_async(self, done=None) -> bool:
        def work(progress, cancelled):
            progress(1, 1, "Reading template index")
            slots = self.backend.index_table()
            return OpResult(True, f"{len(slots)} template(s) on the sensor.",
                            {"slots": slots})

        return self._run("index", work, None, done)

    def enroll_async(self, slot: int, progress=None, done=None) -> bool:
        def work(prog, cancelled):
            self.backend.enroll(slot, prog, cancelled)
            if self.events:
                self.events.info("fingerprint.enroll", f"template stored in slot {slot}")
            return OpResult(True, f"Fingerprint stored in slot {slot}.", {"slot": slot})

        return self._run("enroll", work, progress, done)

    def delete_async(self, slot: int, done=None) -> bool:
        def work(progress, cancelled):
            progress(1, 1, f"Deleting slot {slot}")
            self.backend.delete(slot)
            if self.events:
                self.events.warning(
                    "fingerprint.erase", f"template erased from slot {slot}"
                )
            return OpResult(True, f"Slot {slot} erased from the sensor.", {"slot": slot})

        return self._run("delete", work, None, done)

    def delete_many_async(self, slots, done=None) -> bool:
        """Erase several slots; keeps going past failures and reports them."""
        slots = sorted(int(s) for s in slots)

        def work(progress, cancelled):
            failed: list[tuple[int, str]] = []
            for n, slot in enumerate(slots, 1):
                progress(n, len(slots), f"Deleting slot {slot}")
                try:
                    self.backend.delete(slot)
                except SensorError as exc:
                    failed.append((slot, str(exc)))
            erased = [s for s in slots if s not in {f for f, _ in failed}]
            if self.events and erased:
                self.events.warning("fingerprint.erase", f"templates erased from {erased}")
            if failed:
                return OpResult(False, f"{len(failed)} of {len(slots)} slot(s) could "
                                       f"not be erased: {failed[0][1]}",
                                {"failed": failed, "erased": erased})
            return OpResult(True, f"{len(slots)} slot(s) erased.", {"erased": erased})

        return self._run("delete-many", work, None, done)

    def clear_async(self, done=None) -> bool:
        def work(progress, cancelled):
            progress(1, 1, "Clearing the sensor library")
            self.backend.clear()
            if self.events:
                self.events.warning("fingerprint.clear", "all templates erased")
            return OpResult(True, "All templates erased from the sensor.")

        return self._run("clear", work, None, done)

    def identify_async(self, progress=None, done=None) -> bool:
        min_confidence = self.config.getint("fingerprint", "min_confidence", 50)

        def work(prog, cancelled):
            slot, confidence = self.backend.identify(prog, cancelled)
            if confidence < min_confidence:
                return OpResult(
                    False,
                    f"Match rejected: confidence {confidence} is below the "
                    f"threshold of {min_confidence}.",
                    {"slot": slot, "confidence": confidence, "code": 0x08},
                )
            return OpResult(
                True, f"Matched slot {slot} (confidence {confidence}).",
                {"slot": slot, "confidence": confidence},
            )

        return self._run("identify", work, progress, done)

    # ------------------------------------------------------------------ watch
    def watch(self, on_finger=None, on_result=None) -> int:
        """Scan every finger that touches the sensor until :meth:`release`.

        ``on_finger()`` fires as soon as a finger is down, ``on_result(OpResult)``
        once it has been searched (``data["slot"]`` on a match).  The finger
        must leave the sensor before it is scanned again.  Both run on the
        watch thread.
        """
        with self._lock:
            self._watch_token += 1
            self._watch = _Watch(self._watch_token, on_finger, on_result)
            token = self._watch_token
            start = self._watch_thread is None and self.backend.supports_watch
            if start:
                self._watch_thread = threading.Thread(target=self._watch_loop,
                                                      name="fp-watch", daemon=True)
        if start:
            self._watch_thread.start()
        return token

    def release(self, token: int) -> None:
        """Stop watching, unless another screen has taken over meanwhile."""
        with self._lock:
            if self._watch is not None and self._watch.token == token:
                self._watch = None

    def _current_watch(self, token: int | None = None) -> _Watch | None:
        with self._lock:
            w = self._watch
            if token is not None and (w is None or w.token != token):
                return None
            return w

    def _watch_loop(self) -> None:
        poll = max(0.1, self.config.getfloat("fingerprint", "poll_seconds", 0.3))
        while not self._stop.is_set():
            w = self._current_watch()
            if w is None or self.is_busy():
                self._stop.wait(0.2)
                continue
            try:
                result = self._watch_once(w)
            except NotConfigured:
                return
            except SensorTimeout:
                # Silent module: drop the port so the next try starts clean
                # (re-opening also repeats the password handshake).
                self._set_online(False)
                self._reset_port()
                self._stop.wait(3.0)
                continue
            except SensorError as exc:
                if exc.code is None:  # garbled frame -- resynchronise
                    self._reset_port()
                    self._stop.wait(1.0)
                else:  # e.g. 0x03 a smudged touch; just look again
                    self._set_online(True)
                    self._stop.wait(poll)
                continue
            except Exception as exc:  # serial port vanished, etc.
                was = self._online
                self._set_online(False)
                self._reset_port()
                if self.events and was is not False:
                    self.events.error("fingerprint.error", f"watch: {exc}")
                self._stop.wait(3.0)
                continue
            self._set_online(True)
            if result is not None and self._current_watch(w.token) is not None:
                _call(w.on_result, result)
            self._stop.wait(poll)

    def _watch_once(self, w: _Watch) -> OpResult | None:
        """One look at the sensor.  A result only when a fresh finger was scanned."""
        min_confidence = self.config.getint("fingerprint", "min_confidence", 50)
        with self._sensor_lock:
            if self._current_watch(w.token) is None:
                return None
            if not self.backend.poll_finger():
                w.armed = True
                return None
            if not w.armed:  # still the finger we already answered
                return None
            w.armed = False
            _call(w.on_finger)
            try:
                slot, confidence = self.backend.match_image()
            except SensorTimeout:
                raise
            except SensorError as exc:
                return OpResult(False, str(exc), {"code": exc.code})
        if confidence < min_confidence:
            return OpResult(False, f"Match rejected: confidence {confidence} is below "
                                   f"the threshold of {min_confidence}.",
                            {"slot": slot, "confidence": confidence, "code": 0x08})
        return OpResult(True, f"Matched slot {slot} (confidence {confidence}).",
                        {"slot": slot, "confidence": confidence})

    def _reset_port(self) -> None:
        with self._sensor_lock:
            try:
                self.backend.close()
            except Exception:
                pass

    def close(self) -> None:
        self.cancel()
        self._stop.set()
        with self._lock:
            self._watch = None
            thread = self._watch_thread
        if thread is not None:
            thread.join(timeout=5.0)
        try:
            self.backend.close()
        except Exception:
            pass


def _call(callback, *args) -> None:
    if callback is None:
        return
    try:
        callback(*args)
    except Exception:
        pass
