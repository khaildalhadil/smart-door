#!/usr/bin/env python3
"""Set the door passcode or reset a user's PIN from the terminal.

Recovery path for when nobody can sign in on the panel.  Stop the app first:
it keeps config.ini in memory and would overwrite the change on its next save.

    python3 tools/set_passcode.py                  # new door passcode
    python3 tools/set_passcode.py --user Khalid    # new sign-in PIN for a user
    python3 tools/set_passcode.py --reset-lockout  # clear keypad/sign-in lockouts
"""

from __future__ import annotations

import argparse
import getpass
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.config import CONFIG_PATH, Config  # noqa: E402
from app.events import EventLog  # noqa: E402
from app.security import LoginLimiter, PasscodeManager  # noqa: E402
from app.users import UserStore  # noqa: E402


def ask_twice(label: str, validate) -> str:
    while True:
        first = getpass.getpass(f"New {label}: ")
        err = validate(first)
        if err:
            print(err)
            continue
        if getpass.getpass(f"Repeat {label}: ") != first:
            print("They did not match, try again.")
            continue
        return first


def main() -> int:
    p = argparse.ArgumentParser(description="Set the door passcode or a user PIN")
    p.add_argument("--config", default=str(CONFIG_PATH))
    p.add_argument("--user", help="reset this user's sign-in PIN instead")
    p.add_argument("--reset-lockout", action="store_true",
                   help="only clear the keypad and sign-in lockouts")
    args = p.parse_args()

    config = Config(Path(args.config))
    events = EventLog(config)
    passcode = PasscodeManager(config, events)

    if args.reset_lockout:
        passcode.reset()
        LoginLimiter(config, events).reset()
        events.info("security.reset", "lockouts cleared from the terminal")
        print("Lockouts cleared.")
        return 0

    if args.user:
        users = UserStore(config, events)
        user = users.by_name(args.user)
        if user is None:
            print(f"No user named {args.user!r}. Known: "
                  + ", ".join(u.name for u in users.all_users()))
            return 1
        users.set_pin(user.id, ask_twice(f"PIN for {user.name}", users.validate_pin))
        print(f"PIN updated for {user.name}.")
        return 0

    passcode.set_passcode(ask_twice("door passcode", passcode.validate_new))
    print(f"Door passcode updated in {config.path}.")
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except (KeyboardInterrupt, EOFError):
        print()
        sys.exit(130)
