#!/usr/bin/env python3
"""Add the OpenAI key for WhatsApp's free-text answers (app/ai.py).

Asks for the key without echoing it, sends one tiny request with the model
from config.ini [ai], and only then writes OPENAI_API_KEY into .env (mode
0600).  The app reads the key on every message: no restart needed.

    python3 tools/set_openai_key.py           # paste the key, test it, save it
    python3 tools/set_openai_key.py --check   # test the key already in .env
"""

from __future__ import annotations

import argparse
import getpass
import os
import sys
from pathlib import Path

import requests

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.ai import ApiError  # noqa: E402
from app.config import CONFIG_PATH, ENV_PATH, Config, env_value  # noqa: E402
from app.events import EventLog  # noqa: E402

NAME = "OPENAI_API_KEY"
DEFAULT_BASE = "https://api.openai.com/v1"
KEYS_URL = "https://platform.openai.com/api-keys"
BILLING_URL = "https://platform.openai.com/settings/organization/billing"


def masked(key: str) -> str:
    return f"{key[:7]}…{key[-4:]}" if len(key) > 14 else "…"


def clean(raw: str) -> str:
    """The key as pasted, without quotes, spaces or a leading ``NAME=``."""
    key = raw.strip().strip("'\"").strip()
    if key.startswith(NAME):
        key = key[len(NAME):].lstrip(" =").strip("'\"").strip()
    return key


def test_key(key: str, config: Config) -> tuple[str, str]:
    """``(kind, detail)``: kind is "ok", an ApiError kind, or "network"."""
    base = (config.get("ai", "base_url") or DEFAULT_BASE).rstrip("/")
    payload = {"model": config.get("ai", "model") or "gpt-6-luna",
               "messages": [{"role": "user", "content": "Reply with OK."}],
               "max_completion_tokens": 20}
    effort = config.get("ai", "reasoning_effort").strip()
    if effort:
        payload["reasoning_effort"] = effort
    try:
        for _ in range(2):
            res = requests.post(f"{base}/chat/completions", json=payload,
                                headers={"Authorization": f"Bearer {key}"}, timeout=(10, 40))
            if res.status_code == 400 and "reasoning_effort" in res.text and "reasoning_effort" in payload:
                del payload["reasoning_effort"]     # the app drops it the same way
                continue
            break
    except requests.RequestException as exc:
        return "network", str(exc)[:160]
    if res.status_code == 200:
        return "ok", ""
    err = ApiError(res)
    return err.kind, err.detail


def save_key(key: str, path: Path) -> None:
    """Put ``NAME=key`` in ``path``: replaces an existing line, keeps the rest."""
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except FileNotFoundError:
        lines = []
    out, done = [], False
    for line in lines:
        if line.partition("=")[0].strip() == NAME:
            if not done:
                out.append(f"{NAME}={key}")
                done = True
            continue                              # drop older duplicates
        out.append(line)
    if not done:
        if out and out[-1].strip():
            out.append("")
        out += ["# OpenAI key for WhatsApp free-text answers (app/ai.py).",
                f"{NAME}={key}"]
    tmp = path.with_name(path.name + ".tmp")
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as fh:
        fh.write("\n".join(out) + "\n")
    os.chmod(tmp, 0o600)
    os.replace(tmp, path)


def main() -> int:
    p = argparse.ArgumentParser(description="Add or test the OpenAI key used by WhatsApp")
    p.add_argument("--check", action="store_true", help="only test the key already in .env")
    p.add_argument("--config", default=str(CONFIG_PATH))
    p.add_argument("--env", default=str(ENV_PATH))
    args = p.parse_args()

    config = Config(Path(args.config))
    env_path = Path(args.env)
    model = config.get("ai", "model") or "gpt-6-luna"
    default_base = (config.get("ai", "base_url") or DEFAULT_BASE).rstrip("/") == DEFAULT_BASE

    if not config.getbool("ai", "enabled", True):
        print("Note: the AI is switched off in config.ini ([ai] enabled = false).")

    if args.check:
        key = env_value(NAME, env_path)
        if not key:
            print(f"{NAME} in {env_path} is empty: paste the key right after the = and save.")
            return 1
        if any(ch.isspace() for ch in key):
            print(f"{NAME} in {env_path} contains spaces: copy the key again, in one piece.")
            return 1
    else:
        if sys.stdin.isatty():
            key = clean(getpass.getpass("Paste the OpenAI key, then Enter (it stays hidden): "))
        else:
            key = clean(sys.stdin.readline())
        if not key:
            print("No key received.")
            return 1
        if any(ch.isspace() for ch in key):
            print("The key contains spaces: copy it again, in one piece.")
            return 1
        if default_base and not key.startswith("sk-"):
            print(f"That does not look like an OpenAI key (they start with sk-). Get one at {KEYS_URL}")
            return 1

    print(f"Testing key {masked(key)} ({len(key)} characters) with model {model} ...")
    kind, detail = test_key(key, config)

    if kind == "invalid_api_key":
        print("✗ OpenAI rejected this key: it is wrong, incomplete or deleted."
              + ("" if args.check else " Not saved."))
        print(f"  Make a new key at {KEYS_URL} and copy it right away (it is shown only once).")
        print(f"  ({detail})")
        return 1

    if not args.check:
        save_key(key, env_path)
        EventLog(config).info("ai.key", f"{NAME} saved from the terminal ({kind})")
        print(f"✓ Saved in {env_path} (no restart needed).")

    if kind == "ok":
        print("✓ The key works. Try it on WhatsApp: «جايني ضيف ابي رمز».")
        return 0
    if kind == "insufficient_quota":
        print(f"✗ The key is right but the account has no credit. Add credit at:\n  {BILLING_URL}")
    elif kind == "model_not_found":
        print(f"✗ The key is right but model {model} is not available to this account.\n"
              "  Change [ai] model in config.ini (stop smartlock first, then start it).")
    elif kind == "rate_limit":
        print("… OpenAI is busy right now; try again in a minute: python3 tools/set_openai_key.py --check")
    elif kind == "network":
        print("… Could not reach OpenAI right now (internet?). Try again later with --check")
    else:
        print("✗ OpenAI refused the request.")
    print(f"  ({detail})")
    return 1


if __name__ == "__main__":
    try:
        sys.exit(main())
    except (KeyboardInterrupt, EOFError):
        print()
        sys.exit(130)
