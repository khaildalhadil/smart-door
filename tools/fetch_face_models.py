#!/usr/bin/env python3
"""Download the two face models into models/ and verify their SHA-256.

Both come from the official OpenCV model zoo (github.com/opencv/opencv_zoo).
The digests below are the Git LFS object ids committed in that repository, so
a truncated or tampered download is refused and never reaches models/.

    python3 tools/fetch_face_models.py           # download what is missing
    python3 tools/fetch_face_models.py --check   # verify only, no network
"""

from __future__ import annotations

import argparse
import hashlib
import os
import sys
import urllib.request
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.config import CONFIG_PATH, Config  # noqa: E402
from app.face import MODELS  # noqa: E402

ZOO = "https://media.githubusercontent.com/media/opencv/opencv_zoo/main/models/"


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with open(path, "rb") as fh:
        for block in iter(lambda: fh.read(1 << 20), b""):
            digest.update(block)
    return digest.hexdigest()


def verified(path: Path, size: int, digest: str) -> bool:
    return path.is_file() and path.stat().st_size == size and sha256(path) == digest


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    p.add_argument("--config", default=str(CONFIG_PATH))
    p.add_argument("--check", action="store_true", help="verify only, download nothing")
    args = p.parse_args()

    config = Config(Path(args.config))
    failed = 0
    for option, (zoo_path, size, digest) in MODELS.items():
        dest = config.path_for("face", option)
        if verified(dest, size, digest):
            print(f"ok       {dest}")
            continue
        if args.check:
            print(f"MISSING  {dest}" if not dest.exists() else f"BAD      {dest}")
            failed += 1
            continue
        print(f"fetching {zoo_path} ({size / 1e6:.1f} MB)…")
        dest.parent.mkdir(parents=True, exist_ok=True)
        tmp = dest.with_suffix(dest.suffix + ".part")
        try:
            urllib.request.urlretrieve(ZOO + zoo_path, tmp)
            if not verified(tmp, size, digest):
                print(f"REFUSED  {zoo_path}: size or SHA-256 does not match")
                failed += 1
                continue
            os.replace(tmp, dest)
            print(f"ok       {dest}")
        except OSError as exc:
            print(f"FAILED   {zoo_path}: {exc}")
            failed += 1
        finally:
            tmp.unlink(missing_ok=True)
    return 1 if failed else 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except KeyboardInterrupt:
        print()
        sys.exit(130)
