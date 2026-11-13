#!/usr/bin/env python3
"""Read-only check that the R22 module answers the 0xEF01 fingerprint protocol.

Sends only VfyPwd, ReadSysPara and TempleteNum -- nothing is written to the
module.  On success it records the working port/baud in config.ini and sets
``[fingerprint] enabled = true`` and ``probe_confirmed = true`` so the app
starts using the real sensor.  Stop the app before running this.

    python3 tools/probe_fingerprint.py                 # /dev/serial0 @ 57600
    python3 tools/probe_fingerprint.py --scan          # try common baud rates
    python3 tools/probe_fingerprint.py --port /dev/ttyUSB0 --no-save
"""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.config import CONFIG_PATH, Config  # noqa: E402
from app.r22_driver import R22Sensor, SensorError, SensorTimeout  # noqa: E402

SCAN_BAUDS = [57600, 9600, 19200, 38400, 115200]


def preflight(port: str) -> bool:
    ok = True
    real = os.path.realpath(port)
    if not os.path.exists(port):
        print(f"[!] {port} does not exist. Enable the UART: sudo raspi-config -> "
              "Interface Options -> Serial Port -> login shell: No, hardware: Yes.")
        return False
    if not os.access(real, os.R_OK | os.W_OK):
        print(f"[!] No read/write access to {real}. Add yourself to 'dialout' and log in again.")
        ok = False
    for cmdline in ("/boot/firmware/cmdline.txt", "/boot/cmdline.txt"):
        try:
            text = Path(cmdline).read_text()
        except OSError:
            continue
        if any(f"console={name}" in text for name in ("serial0", "ttyAMA0", "ttyS0")):
            print(f"[!] {cmdline} sends the Linux console to the UART; it will "
                  "garble the sensor. Disable the serial login shell in raspi-config.")
            ok = False
        break
    print(f"[i] {port} -> {real}")
    return ok


def try_baud(port: str, baud: int, address: int, password: int, timeout: float):
    sensor = R22Sensor(port=port, baudrate=baud, address=address, password=password,
                       timeout=timeout)
    try:
        sensor.open()
        sensor.verify_password()
        params = dict(sensor.read_parameters())
        params["templates_stored"] = sensor.template_count()
        return params
    finally:
        sensor.close()


def main() -> int:
    cfg_default = CONFIG_PATH
    p = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    p.add_argument("--config", default=str(cfg_default))
    p.add_argument("--port")
    p.add_argument("--baud", type=int)
    p.add_argument("--scan", action="store_true", help="try common baud rates")
    p.add_argument("--no-save", action="store_true", help="do not modify config.ini")
    args = p.parse_args()

    config = Config(Path(args.config))
    port = args.port or config.get("fingerprint", "port")
    address = config.getint("fingerprint", "address", 0xFFFFFFFF)
    password = config.getint("fingerprint", "password", 0)
    timeout = config.getfloat("fingerprint", "timeout", 2.0)
    first = args.baud or config.getint("fingerprint", "baudrate", 57600)
    bauds = [first] + ([b for b in SCAN_BAUDS if b != first] if args.scan else [])

    if not preflight(port):
        print("Fix the issues above first.")
        return 2

    for baud in bauds:
        print(f"[.] {port} @ {baud} ... ", end="", flush=True)
        try:
            params = try_baud(port, baud, address, password, timeout)
        except SensorTimeout as exc:
            print(f"no answer ({exc})")
            continue
        except SensorError as exc:
            print(f"protocol error: {exc}")
            if exc.code == 0x13:
                print("    The module answered but rejected the password: set "
                      "[fingerprint] password in config.ini.")
                return 3
            continue
        except OSError as exc:
            print(f"cannot open port: {exc}")
            return 2

        print("OK")
        for key, value in params.items():
            print(f"    {key:<18} {value}")
        if args.no_save:
            print("\n--no-save: config.ini left unchanged.")
            return 0
        config.set("fingerprint", "port", port)
        config.set("fingerprint", "baudrate", baud)
        if params.get("capacity"):
            config.set("fingerprint", "capacity", params["capacity"])
        config.set("fingerprint", "enabled", "true")
        config.set("fingerprint", "probe_confirmed", "true")
        config.save()
        print(f"\nSaved to {config.path}: enabled, probe_confirmed, port, baudrate, "
              "capacity.\nStart the app; Menu > Fingerprints > Sync should now work.")
        return 0

    print("\nNo answer at any tried speed. Check, with the power OFF:\n"
          "  * VCC is the voltage the module expects (most are 3.3 V)\n"
          "  * module TX -> Pi GPIO15 (pin 10, RXD), module RX -> Pi GPIO14 (pin 8, TXD)\n"
          "  * GND shared with the Pi\n"
          "  * then retry with --scan")
    return 1


if __name__ == "__main__":
    sys.exit(main())
