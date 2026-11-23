#!/usr/bin/env python3
"""Check that the PN532 card reader answers on I2C, then switch it on.

Asks the reader for its firmware version (nothing is written to a card).  On
success it sets ``[card] enabled = true`` in config.ini, so the door app uses
the reader from its next start, and then waits a few seconds for a card to
show that reading works.

Stop the door app first: two programs talking to the reader at once garble
each other's messages.

    systemctl --user stop smartlock
    python3 tools/probe_card.py               # bus 1, address 0x24
    python3 tools/probe_card.py --no-save     # check only
    systemctl --user start smartlock
"""

from __future__ import annotations

import argparse
import os
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.config import CONFIG_PATH, Config  # noqa: E402
from app.nfc import PN532, I2CTransport, ReaderError  # noqa: E402


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--bus", type=int, default=1)
    ap.add_argument("--address", type=lambda v: int(v, 0), default=0x24)
    ap.add_argument("--wait", type=float, default=15, help="seconds to wait for a card")
    ap.add_argument("--no-save", action="store_true", help="do not change config.ini")
    args = ap.parse_args()

    dev = f"/dev/i2c-{args.bus}"
    if not os.path.exists(dev):
        print(f"[!] {dev} is missing: turn I2C on (sudo raspi-config -> Interface Options -> I2C).")
        return 1
    if not os.access(dev, os.R_OK | os.W_OK):
        print(f"[!] No access to {dev}: add yourself to the 'i2c' group and log in again.")
        return 1
    try:
        reader = PN532(I2CTransport(args.bus, args.address))
    except OSError as exc:
        print(f"[!] Cannot open {dev}: {exc}")
        return 1
    try:
        try:
            ic, ver, rev, support = reader.firmware()
        except ReaderError as exc:
            print(f"[!] No answer at 0x{args.address:02x}: {exc}\n"
                  "    Check: mode switch on I2C (then power-cycle the reader), VCC on 3.3V,\n"
                  "    GND, SDA on pin 3, SCL on pin 5.  `i2cdetect -y 1` should show 24.")
            return 1
        if ic != 0x32:
            print(f"[!] Something answered, but it is not a PN532 (chip 0x{ic:02x}).")
            return 1
        print(f"[ok] PN532 firmware {ver}.{rev} (support 0x{support:02x}) at 0x{args.address:02x}")
        reader.configure()

        if not args.no_save:
            config = Config(CONFIG_PATH)
            config.set("card", "bus", args.bus)
            config.set("card", "address", f"0x{args.address:02x}")
            config.set_and_save("card", "enabled", "true")
            print(f"[ok] {CONFIG_PATH}: [card] enabled = true")

        if args.wait > 0:
            print(f"Hold a card on the reader ({args.wait:g} s)...")
            end = time.monotonic() + args.wait
            while time.monotonic() < end:
                try:
                    uid = reader.read_uid()
                except ReaderError as exc:
                    print(f"[!] Read failed: {exc}")
                    return 1
                if uid:
                    print(f"[ok] Card read: {len(uid)}-byte number ending …{uid.hex().upper()[-4:]}")
                    break
                time.sleep(0.2)
            else:
                print("[--] No card seen (the reader itself works).")
    finally:
        reader.close()
    if not args.no_save:
        print("Start the door app again: systemctl --user start smartlock")
    return 0


if __name__ == "__main__":
    sys.exit(main())
