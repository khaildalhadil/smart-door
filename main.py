#!/usr/bin/env python3
"""Smart Lock -- touch keypad, fingerprint, face and card door controller for Raspberry Pi.

    python3 main.py                      # kiosk mode, settings from config.ini
    python3 main.py --windowed           # normal window (desktop testing)
    python3 main.py --simulate           # never touch GPIO, even on a Pi
    python3 main.py --simulate-fingerprint
    python3 main.py --simulate-card      # card = hex number in data/sim_card.txt

The relay is forced to the locked level before the window opens and again on
every exit path (normal quit, SIGTERM, Ctrl+C, unhandled exception).
"""

from __future__ import annotations

import argparse
import atexit
import os
import signal
import sys
import tkinter as tk
import traceback
from pathlib import Path

from app.alerts import AlertQueue
from app.api import ApiServer
from app.buzzer import Buzzer
from app.cloud import CloudSync
from app.commands import CommandRouter
from app.config import CONFIG_PATH, Config
from app.doorbell import Doorbell
from app.events import EventLog
from app.face import FaceService
from app.fingerprint import FingerprintService, SimulatedBackend
from app.hazard import HazardMonitor
from app.history import History
from app.lock import LockController, SimulationBackend
from app.nfc import CardService
from app.recorder import Recorder
from app.security import LoginLimiter, PasscodeManager
from app.temp_codes import TempCodeStore
from app.ui.app import App, Context
from app.users import UserStore


def parse_args(argv=None) -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Raspberry Pi smart lock")
    p.add_argument("--config", default=str(CONFIG_PATH),
                   help="path to config.ini (created with defaults if missing)")
    p.add_argument("--windowed", action="store_true",
                   help="run in a normal window instead of fullscreen")
    p.add_argument("--simulate", action="store_true",
                   help="simulate the relay; GPIO is never touched")
    p.add_argument("--simulate-fingerprint", action="store_true",
                   help="use the fake fingerprint sensor")
    p.add_argument("--simulate-card", action="store_true",
                   help="use the fake card reader (data/sim_card.txt)")
    return p.parse_args(argv)


def main(argv=None) -> int:
    args = parse_args(argv)
    config = Config(Path(args.config))
    events = EventLog(config)
    # The structured history (rows + pictures) listens to the event log from
    # the very first line; its worker starts once the camera exists.
    history = History(config, events)
    recorder = Recorder(config, events, history)
    events.info("app.start", f"config={config.path}")

    relay = None
    if args.simulate:
        relay = SimulationBackend("--simulate")
        events.warning("lock.backend", "simulation forced by --simulate")
    lock = LockController(config, events, backend=relay)
    atexit.register(lock.close)

    users = UserStore(config, events)
    sensor = None
    if args.simulate_fingerprint:
        data_dir = config.path_for("logging", "file").parent
        sensor = SimulatedBackend(config, data_dir / "sim_fingerprints.json")
        events.info("fingerprint.backend", "simulated sensor (--simulate-fingerprint)")
    fingerprints = FingerprintService(config, users, events, backend=sensor)
    cards = CardService(config, users, events, simulate=args.simulate_card)
    cards.start()
    temp_codes = TempCodeStore(config, events)
    faces = FaceService(config, users, events)
    faces.start()
    # Fire / smoke sensors: they open the door and queue WhatsApp alerts, and
    # work whether or not the screen is up.
    alerts = AlertQueue(config, events)
    hazards = HazardMonitor(config, lock, alerts, events)
    buzzer = Buzzer(config, events)
    if args.simulate:
        buzzer.enabled = False  # --simulate never touches GPIO
    buzzer.start()
    hazards.add_listener(buzzer.on_hazards)
    hazards.start()
    doorbell = Doorbell(config, events, lock)
    # Rows and pictures go to Supabase when .env has its keys; otherwise they
    # just stay in data/history.db.
    cloud = CloudSync(config, events, history)
    recorder.start(alerts=alerts, faces=faces, cloud=cloud)

    ctx = Context(
        config=config,
        events=events,
        passcode=PasscodeManager(config, events),
        login=LoginLimiter(config, events),
        lock=lock,
        users=users,
        fingerprints=fingerprints,
        cards=cards,
        temp_codes=temp_codes,
        faces=faces,
        alerts=alerts,
        hazards=hazards,
        history=history,
        doorbell=doorbell,
        cloud=cloud,
    )
    ctx.commands = CommandRouter(ctx)

    def live_status() -> dict:
        """The heartbeat row the dashboard shows as "device online"."""
        rows, photos = history.backlog()
        return {
            "lock": "unlocked" if lock.status().is_unlocked else "locked",
            "alarm": "+".join(hazards.alarms()),
            "camera": bool(faces.available and faces.camera_online),
            "bot": alerts.bot_online(),
            "relay": lock.backend.name,
            "fingerprint": fingerprints.online if fingerprints.available else None,
            "card": cards.online if cards.available else None,
            "sensors": hazards.states(),
            "pending_alerts": len(alerts.pending()),
            "backlog_rows": rows,
            "backlog_photos": photos,
        }

    cloud.start(status=live_status)

    def shutdown() -> None:
        """Every exit path: stop the helpers, then lock the door."""
        doorbell.close()
        hazards.close()
        buzzer.close()
        faces.close()
        temp_codes.close()
        fingerprints.close()
        cards.close()
        lock.close()
        recorder.close()   # after the lock, so "relay released" is recorded too
        cloud.close()
        history.close()

    # Started from an SSH / VS Code terminal: use the Pi's own screen.
    if not os.environ.get("DISPLAY") and Path("/tmp/.X11-unix/X0").exists():
        os.environ["DISPLAY"] = ":0"
    try:
        root = tk.Tk()
    except tk.TclError as exc:
        print(f"Cannot open the display: {exc}\n"
              "Run this from the Pi's desktop session (or set DISPLAY).",
              file=sys.stderr)
        shutdown()
        return 1

    def report(exc_type, value, tb):
        events.error("ui.exception", f"{exc_type.__name__}: {value}")
        traceback.print_exception(exc_type, value, tb)

    root.report_callback_exception = report
    app = App(root, ctx, fullscreen=False if args.windowed else None)

    def on_signal(signum, _frame):
        events.info("app.signal", signal.Signals(signum).name)
        app.post(app.quit)

    signal.signal(signal.SIGTERM, on_signal)
    signal.signal(signal.SIGINT, on_signal)

    # Remote API for the WhatsApp bot; only once the door panel itself is up.
    api = ApiServer(ctx)
    if config.getbool("api", "enabled", True):
        api.start()

    try:
        root.mainloop()
    finally:
        api.close()
        shutdown()
    return 0


if __name__ == "__main__":
    sys.exit(main())
