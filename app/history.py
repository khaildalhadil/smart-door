"""Structured door history: every entry, refusal, doorbell ring and alarm,
with the camera picture taken at that moment.

``data/events.log`` stays the human-readable audit trail.  This is the same
story in a form that can be queried -- ``data/history.db`` (SQLite) plus JPEG
files in ``data/photos/`` -- and it is what these use:

* the WhatsApp commands "log" and "summary" (app/commands.py);
* the bot, which fetches a picture with ``GET /photos/<name>``;
* the cloud sync (app/cloud.py), which copies rows and pictures to Supabase
  for the web dashboard.  Every row carries ``synced`` / ``photo_synced``
  flags, so the table is also the upload queue: nothing is lost while the
  internet is down.

Rows are written by app/recorder.py, which listens to the event log.
"""

from __future__ import annotations

import os
import re
import secrets
import sqlite3
import threading
import time
import uuid
from pathlib import Path

from .config import Config

# What a row is about.  The dashboard filters and charts on these.
ENTRY = "entry"          # the door was opened for someone
DENIED = "denied"        # someone tried and was refused
DOORBELL = "doorbell"    # the bell button, and how the owner answered
ALARM = "alarm"          # fire / smoke
REMOTE = "remote"        # a WhatsApp command (other than the unlock itself)
ADMIN = "admin"          # users, door code, sign-ins on the panel
SYSTEM = "system"        # start, shutdown, faults
CATEGORIES = (ENTRY, DENIED, DOORBELL, ALARM, REMOTE, ADMIN, SYSTEM)

# data/photos/20261113-182501-keypad-3f9a2b1c.jpg
PHOTO_NAME = re.compile(r"^\d{8}-\d{6}-[a-z0-9_]{1,24}-[0-9a-f]{8}\.jpg$")

_SCHEMA = """
CREATE TABLE IF NOT EXISTS events (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    uid           TEXT    NOT NULL UNIQUE,     -- key of the row in Supabase
    ts            REAL    NOT NULL,            -- Unix time
    category      TEXT    NOT NULL,
    method        TEXT    NOT NULL DEFAULT '', -- keypad, face, fingerprint, ...
    person        TEXT    NOT NULL DEFAULT '', -- user name, when known
    event         TEXT    NOT NULL DEFAULT '', -- event-log name: lock.unlock, ...
    detail        TEXT    NOT NULL DEFAULT '', -- event-log message
    level         TEXT    NOT NULL DEFAULT 'INFO',
    photo         TEXT    NOT NULL DEFAULT '', -- file name in data/photos
    synced        INTEGER NOT NULL DEFAULT 0,  -- row is in Supabase
    photo_synced  INTEGER NOT NULL DEFAULT 0   -- picture is in the bucket
);
CREATE INDEX IF NOT EXISTS events_ts ON events (ts);
CREATE INDEX IF NOT EXISTS events_queue ON events (synced, photo_synced);
"""

def cloud_photo_path(name: str) -> str:
    """Where a picture lives in the bucket: ``2026/11/<name>``."""
    return f"{name[:4]}/{name[4:6]}/{name}"


def start_of_day(now: float | None = None) -> float:
    """Unix time of the last local midnight."""
    t = time.localtime(time.time() if now is None else now)
    return time.mktime((t.tm_year, t.tm_mon, t.tm_mday, 0, 0, 0, 0, 0, -1))


class History:
    """Thread-safe.  One SQLite connection, guarded by a lock."""

    def __init__(self, config: Config, events=None):
        self.config = config
        self.events = events
        data_dir = config.path_for("logging", "file").parent
        self.db_path: Path = data_dir / "history.db"
        self.photo_dir: Path = data_dir / "photos"
        self._lock = threading.RLock()
        data_dir.mkdir(parents=True, exist_ok=True)
        self.photo_dir.mkdir(mode=0o700, exist_ok=True)
        # The file holds who came and went: owner-only, like users.json.
        if not self.db_path.exists():
            os.close(os.open(self.db_path, os.O_WRONLY | os.O_CREAT, 0o600))
        self._db = sqlite3.connect(self.db_path, check_same_thread=False,
                                   isolation_level=None, timeout=10)
        self._db.row_factory = sqlite3.Row
        with self._lock:
            self._db.execute("PRAGMA journal_mode = WAL")
            self._db.execute("PRAGMA synchronous = NORMAL")
            self._db.executescript(_SCHEMA)

    # ------------------------------------------------------------------ writes
    def add(self, category: str, method: str = "", person: str = "", event: str = "",
            detail: str = "", level: str = "INFO", photo: str = "",
            ts: float | None = None) -> dict:
        """Store one row and return it."""
        row = {
            "uid": uuid.uuid4().hex,
            "ts": time.time() if ts is None else float(ts),
            "category": category if category in CATEGORIES else SYSTEM,
            "method": method, "person": person, "event": event,
            "detail": detail[:500], "level": level, "photo": photo,
            "synced": 0,
            # Nothing to upload when there is no picture.
            "photo_synced": 0 if photo else 1,
        }
        with self._lock:
            cur = self._db.execute(
                "INSERT INTO events (uid, ts, category, method, person, event, detail,"
                " level, photo, synced, photo_synced) VALUES (:uid, :ts, :category,"
                " :method, :person, :event, :detail, :level, :photo, :synced,"
                " :photo_synced)", row)
            row["id"] = cur.lastrowid
        return row

    def save_photo(self, jpeg: bytes, tag: str, ts: float | None = None) -> str:
        """Write a JPEG into data/photos and return its file name ("" on failure)."""
        stamp = time.strftime("%Y%m%d-%H%M%S",
                              time.localtime(time.time() if ts is None else ts))
        tag = re.sub(r"[^a-z0-9_]", "", tag.lower())[:24] or "photo"
        name = f"{stamp}-{tag}-{secrets.token_hex(4)}.jpg"
        try:
            self.photo_dir.mkdir(mode=0o700, exist_ok=True)
            fd = os.open(self.photo_dir / name, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
            with os.fdopen(fd, "wb") as fh:
                fh.write(jpeg)
        except OSError as exc:
            if self.events:
                self.events.error("photo.save", f"cannot write {name}: {exc}")
            return ""
        return name

    def photo_path(self, name: str) -> Path | None:
        """Path of a stored picture, or None if the name is not one of ours.

        The strict pattern is what keeps ``GET /photos/<name>`` inside the
        photo directory.
        """
        if not isinstance(name, str) or not PHOTO_NAME.match(name):
            return None
        path = self.photo_dir / name
        return path if path.is_file() else None

    # ------------------------------------------------------------------- reads
    def recent(self, limit: int = 20, categories=None, since: float | None = None,
               before_id: int | None = None) -> list[dict]:
        """Newest first."""
        sql, args = "SELECT * FROM events WHERE 1=1", []
        if categories:
            sql += f" AND category IN ({','.join('?' * len(categories))})"
            args += list(categories)
        if since is not None:
            sql += " AND ts >= ?"
            args.append(since)
        if before_id is not None:
            sql += " AND id < ?"
            args.append(before_id)
        sql += " ORDER BY id DESC LIMIT ?"
        args.append(max(1, min(int(limit), 500)))
        with self._lock:
            return [dict(r) for r in self._db.execute(sql, args)]

    def counts(self, since: float) -> dict[tuple[str, str], int]:
        """``(category, method) -> rows`` since a Unix time."""
        with self._lock:
            rows = self._db.execute(
                "SELECT category, method, COUNT(*) AS n FROM events WHERE ts >= ?"
                " GROUP BY category, method", (since,)).fetchall()
        return {(r["category"], r["method"]): r["n"] for r in rows}

    def summary(self, since: float) -> dict:
        """The numbers behind the "summary" command and the status report."""
        with self._lock:
            db = self._db
            entries = {r[0]: r[1] for r in db.execute(
                "SELECT method, COUNT(*) FROM events WHERE ts >= ? AND category = ?"
                " GROUP BY method", (since, ENTRY))}
            denied = {r[0]: r[1] for r in db.execute(
                "SELECT method, COUNT(*) FROM events WHERE ts >= ? AND category = ?"
                " GROUP BY method", (since, DENIED))}
            rings = db.execute(
                "SELECT COUNT(*) FROM events WHERE ts >= ? AND event = 'doorbell.ring'",
                (since,)).fetchone()[0]
            alarms = db.execute(
                "SELECT COUNT(*) FROM events WHERE ts >= ? AND category = ?"
                " AND detail LIKE '%DETECTED%'", (since, ALARM)).fetchone()[0]
            last = db.execute(
                "SELECT * FROM events WHERE category = ? ORDER BY id DESC LIMIT 1",
                (ENTRY,)).fetchone()
        return {"entries": entries, "denied": denied, "rings": rings, "alarms": alarms,
                "last_entry": dict(last) if last else None}

    def total(self) -> int:
        with self._lock:
            return self._db.execute("SELECT COUNT(*) FROM events").fetchone()[0]

    # -------------------------------------------------------------- sync queue
    def pending_photos(self, limit: int = 5) -> list[str]:
        """Names of the pictures not in the bucket yet, oldest first."""
        with self._lock:
            return [r[0] for r in self._db.execute(
                "SELECT photo FROM events WHERE photo_synced = 0 AND photo != ''"
                " GROUP BY photo ORDER BY MIN(id) LIMIT ?", (limit,))]

    def pending_rows(self, limit: int = 100) -> list[dict]:
        """Rows not in Supabase yet whose picture (if any) is already there, so
        the dashboard never shows a row pointing at a missing picture."""
        with self._lock:
            return [dict(r) for r in self._db.execute(
                "SELECT * FROM events WHERE synced = 0 AND photo_synced = 1"
                " ORDER BY id LIMIT ?", (limit,))]

    def mark_photo_synced(self, photo: str, lost: bool = False) -> None:
        """The picture is in the bucket: every row showing it may go up now
        (events a moment apart share one picture).

        ``lost``: the file is gone, so those rows go up without a picture.
        """
        with self._lock:
            if lost:
                self._db.execute("UPDATE events SET photo = '', photo_synced = 1,"
                                 " synced = 0 WHERE photo = ? AND photo_synced = 0",
                                 (photo,))
            else:
                self._db.execute("UPDATE events SET photo_synced = 1 WHERE photo = ?",
                                 (photo,))

    def mark_rows_synced(self, row_ids: list[int]) -> None:
        with self._lock:
            self._db.executemany("UPDATE events SET synced = 1 WHERE id = ?",
                                 [(i,) for i in row_ids])

    def backlog(self) -> tuple[int, int]:
        """``(rows, pictures)`` still waiting for the cloud."""
        with self._lock:
            rows = self._db.execute(
                "SELECT COUNT(*) FROM events WHERE synced = 0").fetchone()[0]
            photos = self._db.execute(
                "SELECT COUNT(*) FROM events WHERE photo_synced = 0").fetchone()[0]
        return rows, photos

    # --------------------------------------------------------------- retention
    def cleanup(self) -> tuple[int, int]:
        """Apply ``[photos] keep_days / max_files`` and ``[history] keep_days``.

        Returns ``(pictures deleted, rows deleted)``.
        """
        cfg = self.config
        keep_days = cfg.getfloat("photos", "keep_days", 30.0)
        max_files = cfg.getint("photos", "max_files", 3000)
        try:
            files = sorted(p for p in self.photo_dir.iterdir()
                           if PHOTO_NAME.match(p.name))  # names sort by time
        except OSError:
            files = []
        doomed: list[Path] = []
        if max_files > 0 and len(files) > max_files:
            doomed, files = files[:len(files) - max_files], files[len(files) - max_files:]
        if keep_days > 0:
            cutoff = time.strftime("%Y%m%d-%H%M%S",
                                   time.localtime(time.time() - keep_days * 86400))
            doomed += [p for p in files if p.name[:15] < cutoff]
        removed = 0
        for path in doomed:
            try:
                path.unlink()
                removed += 1
            except OSError:
                continue
            # A picture that never reached the cloud leaves its row without one.
            with self._lock:
                self._db.execute(
                    "UPDATE events SET photo = '', photo_synced = 1, synced = 0"
                    " WHERE photo = ? AND photo_synced = 0", (path.name,))
        rows = 0
        row_days = cfg.getfloat("history", "keep_days", 365.0)
        if row_days > 0:
            with self._lock:
                rows = self._db.execute("DELETE FROM events WHERE ts < ?",
                                        (time.time() - row_days * 86400,)).rowcount
        if (removed or rows) and self.events:
            self.events.info("history.cleanup",
                             f"{removed} old picture(s), {rows} old row(s) deleted")
        return removed, rows

    def close(self) -> None:
        with self._lock:
            try:
                self._db.close()
            except sqlite3.Error:
                pass


def public_row(row: dict) -> dict:
    """A history row as the API returns it."""
    return {
        "id": row["id"],
        "time": time.strftime("%Y-%m-%dT%H:%M:%S%z", time.localtime(row["ts"])),
        "category": row["category"],
        "method": row["method"],
        "person": row["person"],
        "event": row["event"],
        "detail": row["detail"],
        "photo": f"/photos/{row['photo']}" if row["photo"] else None,
    }


__all__ = ["History", "CATEGORIES", "ENTRY", "DENIED", "DOORBELL", "ALARM", "REMOTE",
           "ADMIN", "SYSTEM", "PHOTO_NAME", "cloud_photo_path", "start_of_day",
           "public_row"]
