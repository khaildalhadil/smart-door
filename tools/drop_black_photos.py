#!/usr/bin/env python3
"""Find the black pictures (the camera's flat black frames) and delete them.

    python3 tools/drop_black_photos.py            # list them, delete nothing
    python3 tools/drop_black_photos.py --delete   # delete them here and in Supabase

The events stay in the history and on the dashboard, only without a picture.
Safe while the app is running.  New black frames are no longer saved (the
check in app/face.py); this clears the ones taken before it.

Supabase goes first: if it cannot be reached, nothing is deleted here either,
so running it again later finds the same pictures.
"""

from __future__ import annotations

import argparse
import sqlite3
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.config import Config, env_value  # noqa: E402
from app.face import is_blank  # noqa: E402
from app.history import PHOTO_NAME, cloud_photo_path  # noqa: E402

BATCH = 100


def black_pictures(photo_dir: Path) -> list[Path]:
    import cv2

    found = []
    for path in sorted(photo_dir.glob("*.jpg")):
        if not PHOTO_NAME.match(path.name):
            continue
        frame = cv2.imread(str(path), cv2.IMREAD_GRAYSCALE)
        if frame is not None and is_blank(frame):
            found.append(path)
    return found


def chunks(items: list, size: int = BATCH):
    for i in range(0, len(items), size):
        yield items[i:i + size]


def clear_cloud(cfg: Config, uids: list[str], paths: list[str]) -> str:
    """Rows first (the dashboard must never point at a deleted picture), then
    the pictures.  Returns "" when done, else what went wrong."""
    import requests

    from app.cloud import auth_headers

    url = env_value("SUPABASE_URL").rstrip("/")
    key = env_value("SUPABASE_SERVICE_KEY") or env_value("SUPABASE_KEY")
    table = cfg.get("cloud", "table") or "door_events"
    bucket = cfg.get("cloud", "bucket") or "door-photos"
    session = requests.Session()
    session.headers.update(auth_headers(key))
    try:
        for part in chunks(uids):
            r = session.patch(f"{url}/rest/v1/{table}", params={"uid": f"in.({','.join(part)})"},
                              json={"photo": ""}, headers={"Prefer": "return=minimal"}, timeout=25)
            if r.status_code >= 400:
                return f"table {table}: HTTP {r.status_code} {r.text[:160]}"
        for part in chunks(paths):
            r = session.delete(f"{url}/storage/v1/object/{bucket}", json={"prefixes": part},
                               timeout=25)
            if r.status_code >= 400:
                return f"bucket {bucket}: HTTP {r.status_code} {r.text[:160]}"
    except requests.RequestException as exc:
        return f"{type(exc).__name__}: {exc}"
    return ""


def main() -> int:
    parser = argparse.ArgumentParser(description="Delete the black pictures.")
    parser.add_argument("--delete", action="store_true", help="delete them (default: only list)")
    parser.add_argument("--data", type=Path, help="the data folder (default: the app's)")
    parser.add_argument("--no-cloud", action="store_true", help="leave Supabase alone")
    args = parser.parse_args()

    cfg = Config()
    data = args.data or cfg.path_for("logging", "file").parent
    black = black_pictures(data / "photos")
    if not black:
        print("✓ لا توجد صور سوداء.")
        return 0
    names = [p.name for p in black]

    db = sqlite3.connect(data / "history.db", timeout=10, isolation_level=None)
    rows: dict[str, list[tuple[str, int]]] = {}
    for part in chunks(names):
        marks = ",".join("?" * len(part))
        for uid, photo, uploaded in db.execute(
                f"SELECT uid, photo, photo_synced FROM events WHERE photo IN ({marks})", part):
            rows.setdefault(photo, []).append((uid, uploaded))
    uids = [uid for name in names for uid, _ in rows.get(name, [])]
    uploaded = [name for name in names if any(up for _, up in rows.get(name, []))]

    for name in names:
        where = "في Supabase" if name in uploaded else "على الـ Pi فقط"
        events = len(rows.get(name, []))
        print(f"  {name}  ({events} حدث، {where})" if events else f"  {name}  (بدون حدث)")
    print(f"• {len(names)} صورة سوداء، منها {len(uploaded)} في Supabase.")
    if not args.delete:
        print("للحذف: python3 tools/drop_black_photos.py --delete")
        return 0

    cloud = (not args.no_cloud and env_value("SUPABASE_URL")
             and (env_value("SUPABASE_SERVICE_KEY") or env_value("SUPABASE_KEY")))
    if cloud and (uids or uploaded):
        problem = clear_cloud(cfg, uids, [cloud_photo_path(n) for n in uploaded])
        if problem:
            print(f"✗ Supabase: {problem}\n  ما حُذف شيء. أعد المحاولة لاحقًا.")
            return 1
        print(f"✓ Supabase: {len(uids)} حدث صار بدون صورة، و{len(uploaded)} صورة حُذفت.")

    # Without Supabase now, a row already up there goes up again, without its picture.
    resend = 0 if cloud else 1
    db.execute("BEGIN IMMEDIATE")
    db.executemany("UPDATE events SET photo = '', photo_synced = 1,"
                   " synced = CASE WHEN ? THEN 0 ELSE synced END WHERE photo = ?",
                   [(resend, name) for name in names])
    db.execute("COMMIT")
    gone = 0
    for path in black:
        try:
            path.unlink()
            gone += 1
        except OSError as exc:
            print(f"✗ {path.name}: {exc}")
    print(f"✓ حُذفت {gone} صورة سوداء من الـ Pi. الأحداث باقية بدون صورة.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
