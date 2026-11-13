"""Copies the door history to Supabase, for the web dashboard (web/).

    data/history.db   --rows-->      table   door_events
    data/photos/      --pictures-->  bucket  door-photos   (private)
    live state        --heartbeat->  table   door_status   (one row)

Set-up is in ``supabase/schema.sql`` and the README.  The two secrets go in
``.env`` (mode 0600, never committed):

    SUPABASE_URL=https://xxxxxxxx.supabase.co
    SUPABASE_SERVICE_KEY=sb_secret_...     (or the legacy service_role key)

Without them this module does nothing and the history simply stays on the Pi;
rows written meanwhile go up as soon as the keys are there.  The same holds
while the internet is down: ``history.db`` is the queue, so nothing is lost.

Plain HTTPS through ``requests``: there is no Supabase package to install.
Rows are upserted on their ``uid``, so sending one twice (a reply lost on the
way back) changes nothing.

The secret key bypasses row-level security: it belongs on this Pi only.  The
dashboard uses the *publishable* key plus a signed-in user instead.
"""

from __future__ import annotations

import threading
import time
from datetime import datetime, timezone
from urllib.parse import quote

from .config import Config, env_value
from .history import cloud_photo_path

CONNECT_TIMEOUT, READ_TIMEOUT = 6.0, 25.0
PHOTO_BATCH, ROW_BATCH = 4, 100
RETENTION_EVERY = 24 * 3600.0

STATE_OFF = "off"          # not configured
STATE_OK = "ok"
STATE_OFFLINE = "offline"  # no internet / Supabase unreachable
STATE_ERROR = "error"      # Supabase answered "no": key, table or bucket


class CloudRefused(Exception):
    """Supabase answered with an error that retrying will not fix."""


def iso_utc(ts: float) -> str:
    return datetime.fromtimestamp(ts, timezone.utc).isoformat(timespec="milliseconds")


def auth_headers(key: str) -> dict:
    """New keys (``sb_secret_...``) go in ``apikey`` only; the legacy JWT keys
    are also expected as a bearer token."""
    headers = {"apikey": key}
    if not key.startswith("sb_"):
        headers["Authorization"] = f"Bearer {key}"
    return headers


class CloudSync:
    def __init__(self, config: Config, events, history, url: str | None = None,
                 key: str | None = None):
        self.config = config
        self.events = events
        self.history = history
        self.url = (env_value("SUPABASE_URL") if url is None else url).rstrip("/")
        self.key = (env_value("SUPABASE_SERVICE_KEY") or env_value("SUPABASE_KEY")
                    ) if key is None else key
        self.table = config.get("cloud", "table") or "door_events"
        self.status_table = config.get("cloud", "status_table") or "door_status"
        self.bucket = config.get("cloud", "bucket") or "door-photos"
        self.sync_seconds = max(1.0, config.getfloat("cloud", "sync_seconds", 5.0))
        self.status_seconds = max(15.0, config.getfloat("cloud", "status_seconds", 60.0))
        # Plain http only for a local Supabase (``supabase start``): the secret
        # key must not cross a network unencrypted.
        safe = self.url.startswith(("https://", "http://127.0.0.1:", "http://localhost:"))
        self.enabled = bool(config.getbool("cloud", "enabled", True) and safe and self.key)
        self._state = STATE_OFF
        self._problem = ""
        self._status_provider = None
        self._started_at = time.time()
        self._next_status = 0.0
        self._next_retention = time.monotonic() + 300.0
        self._wake = threading.Event()
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._session = None

    # ------------------------------------------------------------------ set-up
    def start(self, status=None) -> bool:
        """``status()`` returns the live state for the heartbeat row."""
        self._status_provider = status
        if not self.enabled:
            if self.config.getbool("cloud", "enabled", True):
                self.events.info("cloud.off", "SUPABASE_URL / SUPABASE_SERVICE_KEY not set "
                                              "in .env; history stays on this Pi")
            return False
        import requests

        self._session = requests.Session()
        self._session.headers.update(auth_headers(self.key))
        self._thread = threading.Thread(target=self._run, name="cloud", daemon=True)
        self._thread.start()
        self.events.info("cloud.start", f"syncing to {self.url} (table {self.table}, "
                                        f"bucket {self.bucket})")
        return True

    def close(self) -> None:
        self._stop.set()
        self._wake.set()
        if self._thread is not None:
            self._thread.join(timeout=3.0)

    def kick(self) -> None:
        """There is something new to send."""
        self._wake.set()

    def state(self) -> str:
        return self._state if self.enabled else STATE_OFF

    def problem(self) -> str:
        """Why the last sync failed ("" when it did not)."""
        return self._problem

    # -------------------------------------------------------------------- loop
    def _run(self) -> None:
        import requests

        delay = 0.0
        while not self._stop.is_set():
            more = False
            try:
                more = self._sync_once()
                self._set_state(STATE_OK, "")
                delay = 0.0
            except CloudRefused as exc:
                self._set_state(STATE_ERROR, str(exc))
                delay = min(300.0, max(30.0, delay * 2))
            except requests.RequestException as exc:
                self._set_state(STATE_OFFLINE, f"{type(exc).__name__}: {exc}"[:200])
                delay = min(120.0, max(5.0, delay * 2))
            except Exception as exc:  # a bug here must not end the sync for good
                self._set_state(STATE_ERROR, f"{type(exc).__name__}: {exc}"[:200])
                delay = 60.0
            if more and not delay:
                continue
            self._wake.clear()
            if delay:
                self._stop.wait(delay)   # a failing link is not retried on every event
            else:
                self._wake.wait(self.sync_seconds)

    def _set_state(self, state: str, problem: str) -> None:
        before, self._state = self._state, state
        changed = problem != self._problem
        self._problem = problem
        if state == STATE_OK:
            if before in (STATE_OFFLINE, STATE_ERROR):
                self.events.info("cloud.online", "Supabase reachable again")
        elif state == STATE_OFFLINE:
            if before != STATE_OFFLINE:
                self.events.warning("cloud.offline", problem)
        elif changed:
            self.events.error("cloud.error", problem)

    def _sync_once(self) -> bool:
        """One pass.  True when more is waiting than one pass sends."""
        history = self.history
        # Rows first: a problem with the bucket must not hold back the rows
        # that have no picture.  A row with a picture waits for its picture.
        rows = history.pending_rows(ROW_BATCH)
        if rows:
            self._upsert(self.table, "uid", [self._cloud_row(r) for r in rows])
            history.mark_rows_synced([r["id"] for r in rows])
        photos = history.pending_photos(PHOTO_BATCH)
        for name in photos:
            path = history.photo_path(name)
            if path is None:                      # deleted before it could go up
                history.mark_photo_synced(name, lost=True)
                continue
            self._upload(cloud_photo_path(name), path.read_bytes())
            history.mark_photo_synced(name)
        now = time.monotonic()
        if now >= self._next_status:
            self._heartbeat()
            self._next_status = now + self.status_seconds
        if now >= self._next_retention:
            self._next_retention = now + RETENTION_EVERY
            self._retention()
        # Pictures just sent have freed their rows: go round again for those.
        return bool(photos) or len(rows) == ROW_BATCH

    @staticmethod
    def _cloud_row(row: dict) -> dict:
        return {
            "uid": row["uid"],
            "ts": iso_utc(row["ts"]),
            "category": row["category"],
            "method": row["method"],
            "person": row["person"],
            "event": row["event"],
            "detail": row["detail"],
            "level": row["level"],
            "photo": cloud_photo_path(row["photo"]) if row["photo"] else "",
        }

    # --------------------------------------------------------------------- HTTP
    def _check(self, response, what: str) -> None:
        if response.status_code < 400:
            return
        body = " ".join(response.text.split())[:240]
        message = f"{what}: HTTP {response.status_code} {body}"
        if response.status_code in (408, 425, 429) or response.status_code >= 500:
            import requests

            raise requests.RequestException(message)   # worth retrying soon
        raise CloudRefused(message)

    def _upsert(self, table: str, key: str, rows: list[dict]) -> None:
        response = self._session.post(
            f"{self.url}/rest/v1/{quote(table)}", params={"on_conflict": key}, json=rows,
            headers={"Prefer": "resolution=merge-duplicates,return=minimal"},
            timeout=(CONNECT_TIMEOUT, READ_TIMEOUT))
        self._check(response, f"table {table}")

    def _upload(self, path: str, data: bytes) -> None:
        response = self._session.post(
            f"{self.url}/storage/v1/object/{quote(self.bucket)}/{quote(path)}", data=data,
            headers={"Content-Type": "image/jpeg", "x-upsert": "true"},
            timeout=(CONNECT_TIMEOUT, READ_TIMEOUT))
        self._check(response, f"bucket {self.bucket}")

    def _heartbeat(self) -> None:
        info = {}
        if self._status_provider is not None:
            try:
                info = dict(self._status_provider())
            except Exception as exc:
                info = {"error": f"{type(exc).__name__}: {exc}"}
        row = {
            "id": "door",
            "updated_at": iso_utc(time.time()),
            "lock": info.pop("lock", ""),
            "alarm": info.pop("alarm", ""),
            "camera": info.pop("camera", None),
            "bot": info.pop("bot", None),
            "info": {**info, "started_at": iso_utc(self._started_at)},
        }
        self._upsert(self.status_table, "id", [row])

    def _retention(self) -> None:
        """Delete pictures older than ``[cloud] photo_keep_days`` from the bucket."""
        days = self.config.getfloat("cloud", "photo_keep_days", 90.0)
        if days <= 0:
            return
        timeouts = (CONNECT_TIMEOUT, READ_TIMEOUT)
        response = self._session.get(
            f"{self.url}/rest/v1/{quote(self.table)}",
            params={"select": "uid,photo", "photo": "neq.",
                    "ts": f"lt.{iso_utc(time.time() - days * 86400)}", "limit": "200"},
            timeout=timeouts)
        self._check(response, f"table {self.table}")
        old = [r for r in response.json() if r.get("photo")]
        if not old:
            return
        response = self._session.delete(
            f"{self.url}/storage/v1/object/{quote(self.bucket)}",
            json={"prefixes": [r["photo"] for r in old]}, timeout=timeouts)
        self._check(response, f"bucket {self.bucket}")
        uids = ",".join(r["uid"] for r in old)
        response = self._session.patch(
            f"{self.url}/rest/v1/{quote(self.table)}", params={"uid": f"in.({uids})"},
            json={"photo": ""}, headers={"Prefer": "return=minimal"}, timeout=timeouts)
        self._check(response, f"table {self.table}")
        self.events.info("cloud.cleanup", f"{len(old)} old picture(s) deleted from the bucket")
        if len(old) == 200:
            self._next_retention = time.monotonic() + 60.0   # more to go

    # ------------------------------------------------------------- signed URLs
    def signed_url(self, photo: str, seconds: int = 3600) -> str | None:
        """A temporary public link to a picture already in the bucket, for a
        bot whose WhatsApp provider wants a URL rather than the bytes."""
        if not self.enabled:
            return None
        import requests

        try:
            response = requests.post(
                f"{self.url}/storage/v1/object/sign/{quote(self.bucket)}/"
                f"{quote(cloud_photo_path(photo))}",
                json={"expiresIn": int(seconds)}, headers=auth_headers(self.key),
                timeout=(CONNECT_TIMEOUT, 10.0))
            if response.status_code >= 400:
                return None
            signed = response.json().get("signedURL") or response.json().get("signedUrl")
        except (requests.RequestException, ValueError):
            return None
        return f"{self.url}/storage/v1{signed}" if signed else None
