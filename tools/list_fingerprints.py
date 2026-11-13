#!/usr/bin/env python3
"""Print the registered users and their fingerprint slots.

Reads data/users.json only; it never opens the sensor, so it is safe to run
while the app is running.

    python3 tools/list_fingerprints.py
    python3 tools/list_fingerprints.py --csv > fingerprints.csv
"""

from __future__ import annotations

import argparse
import csv
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.config import CONFIG_PATH, Config  # noqa: E402
from app.users import UserStore  # noqa: E402


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    p.add_argument("--config", default=str(CONFIG_PATH))
    p.add_argument("--csv", action="store_true", help="machine-readable output")
    args = p.parse_args()

    users = UserStore(Config(Path(args.config)))
    rows = [
        (fp.slot, u.name, u.role, "yes" if u.enabled else "no", fp.label, fp.created,
         "MISSING ON SENSOR" if fp.orphaned else "")
        for u, fp in users.all_fingerprints()
    ]

    if args.csv:
        out = csv.writer(sys.stdout)
        out.writerow(["slot", "user", "role", "enabled", "finger", "enrolled", "note"])
        out.writerows(rows)
        return 0

    print(f"{'slot':>4}  {'user':<16} {'role':<6} {'on':<3} {'finger':<14} enrolled")
    print("-" * 66)
    for slot, name, role, enabled, label, created, note in rows:
        print(f"{slot:>4}  {name:<16} {role:<6} {enabled:<3} {label:<14} {created}"
              + (f"  {note}" if note else ""))
    without = [u.name for u in users.all_users() if not u.fingerprints]
    print(f"\n{len(rows)} fingerprint(s), {len(users.all_users())} user(s)."
          + (f"  No fingerprints: {', '.join(without)}" if without else ""))
    return 0


if __name__ == "__main__":
    sys.exit(main())
