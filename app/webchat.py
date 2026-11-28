"""Chat with the door from the web dashboard, like on WhatsApp (admins only).

    dashboard --insert-->  table door_chat   sender 'admin', status 'pending'
    this Pi   --poll---->  claims the row (status 'working'), answers it with
                           app/commands.py (keywords first, OpenAI for free
                           text, with the key in .env), uploads a picture if
                           the answer has one, and inserts the reply
                           (sender 'door'); the dashboard sees it live.

Everything goes through Supabase, so the Pi opens no port and the OpenAI key
never leaves it.  Row-level security (supabase/chat.sql) lets only admins
write, and each admin read only their own conversation.  Before carrying a
message out, the Pi checks again with the secret key that the writer is still
an admin, and refuses a message that waited longer than
``[webchat] stale_seconds`` (written while the door was offline): "افتح"
typed an hour ago must not open the door now.  Ages are measured on
Supabase's clock (the reply's Date header), not the Pi's, which has no RTC.

A message is claimed with a conditional update (pending -> working) before
anything happens, so it is carried out at most once, even if a reply is lost
on the way back.
"""

from __future__ import annotations

import threading
import time
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from urllib.parse import quote

from .cloud import CONNECT_TIMEOUT, READ_TIMEOUT, CloudRefused, auth_headers, check_response
from .history import cloud_photo_path
from .wording import Texts

POLL_BATCH = 5
ROLE_CACHE_SECONDS = 30.0
RETENTION_EVERY = 24 * 3600.0
KEEP_DAYS = 30            # chat rows older than this are deleted from Supabase
MAX_TEXT = 1000           # longest message the dashboard may send (chat.sql agrees)

STALE = Texts(
    "⏱️ وصلت رسالتك متأخرة (كان النظام غير متصل)، فما نفّذتها. أرسلها مرة ثانية إذا ما زلت تبيها.",
    "⏱️ Your message arrived late (the door was offline), so it was not carried out. "
    "Send it again if you still want it.")
DENIED = Texts(
    "🚫 هذا الحساب لم يعد مديرًا، فما نفّذت الطلب.",
    "🚫 This account is no longer an admin, so nothing was done.")
FAILED = Texts(
    "⚠️ صار خطأ في برنامج الباب أثناء تنفيذ الطلب. جرّب مرة ثانية.",
    "⚠️ The door program hit an error while answering. Try again.")


def _ts(value: str) -> float:
    """Unix time of a PostgREST timestamp ("2026-11-28T11:05:02.1+00:00")."""
    return datetime.fromisoformat(value.replace("Z", "+00:00")).timestamp()


class WebChat:
    """``ctx`` is the app's :class:`app.ui.app.Context`; ``cloud`` its
    :class:`app.cloud.CloudSync` (same Supabase URL and secret key)."""

    def __init__(self, ctx, cloud):
        self.ctx = ctx
        self.cloud = cloud
        cfg = ctx.config
        self.events = ctx.events
        self.table = cfg.get("webchat", "table") or "door_chat"
        self.poll_seconds = max(1.0, cfg.getfloat("webchat", "poll_seconds", 2.0))
        self.stale_seconds = max(10.0, cfg.getfloat("webchat", "stale_seconds", 90.0))
        self.enabled = bool(cfg.getbool("webchat", "enabled", True) and cloud.enabled)
        self._roles: dict[str, tuple[str, float]] = {}
        self._names: dict[str, str] = {}
        self._problem = ""
        self._next_retention = time.monotonic() + 600.0
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._session = None

    # ------------------------------------------------------------------ set-up
    def start(self) -> bool:
        if not self.enabled:
            return False
        import requests

        self._session = requests.Session()
        self._session.headers.update(auth_headers(self.cloud.key))
        self._thread = threading.Thread(target=self._run, name="webchat", daemon=True)
        self._thread.start()
        self.events.info("webchat.start", f"answering dashboard messages (table {self.table})")
        return True

    def close(self) -> None:
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout=3.0)

    # -------------------------------------------------------------------- loop
    def _run(self) -> None:
        import requests

        delay = 0.0
        while not self._stop.is_set():
            try:
                self._poll_once()
                if self._problem:
                    self.events.info("webchat.online", "dashboard chat working again")
                    self._problem = ""
                delay = 0.0
            except CloudRefused as exc:
                text = str(exc)
                if "PGRST205" in text or ("404" in text and self.table in text):
                    text = (f"table {self.table} is missing in Supabase: run supabase/chat.sql "
                            "in the SQL Editor")
                if text != self._problem:
                    self.events.warning("webchat.off", text)
                    self._problem = text
                delay = 30.0   # checks again soon: the owner may be running chat.sql now
            except requests.RequestException:
                delay = min(60.0, max(5.0, delay * 2))   # offline: cloud.py reports it
            except Exception as exc:  # a bug here must not end the chat for good
                text = f"{type(exc).__name__}: {exc}"[:200]
                if text != self._problem:
                    self.events.error("webchat.error", text)
                    self._problem = text
                delay = 30.0
            self._stop.wait(delay or self.poll_seconds)

    def _poll_once(self) -> None:
        response = self._session.get(
            self._rest(), params={"select": "id,created_at,user_id,body", "sender": "eq.admin",
                                  "status": "eq.pending", "order": "id.asc",
                                  "limit": str(POLL_BATCH)},
            timeout=(CONNECT_TIMEOUT, READ_TIMEOUT))
        check_response(response, f"table {self.table}")
        now = self._server_now(response)
        for row in response.json():
            if self._stop.is_set():
                return
            self._handle(row, now)
        if time.monotonic() >= self._next_retention:
            self._next_retention = time.monotonic() + RETENTION_EVERY
            self._retention()

    # ----------------------------------------------------------- one message
    def _handle(self, row: dict, now: float) -> None:
        rid, user = row["id"], row["user_id"]
        if not self._claim(rid):
            return                      # already taken: never carried out twice
        try:
            age = now - _ts(row["created_at"])
            if age > self.stale_seconds:
                self.events.warning("webchat.stale",
                                    f"message #{rid} waited {age:.0f}s: not carried out")
                self._reply(row, STALE.pick(True), STALE.as_dict(), "", "stale", "stale")
                return
            if self._role(user) != "admin":
                self.events.warning("webchat.deny", f"message #{rid}: the writer is not an admin")
                self._reply(row, DENIED.pick(True), DENIED.as_dict(), "", "denied", "error")
                return
            text = (row.get("body") or "")[:MAX_TEXT]
            result = self.ctx.commands.handle(text, sender=self._sender(user), verified=True)
            photo = self._photo(result.get("photo") or "")
            self._reply(row, result.get("reply") or "", result.get("texts") or {}, photo,
                        result.get("intent") or "", "done")
        except CloudRefused:
            raise
        except Exception as exc:
            import requests

            if isinstance(exc, requests.RequestException):
                raise
            self.events.error("webchat.error", f"message #{rid}: {type(exc).__name__}: {exc}"[:200])
            try:
                self._reply(row, FAILED.pick(True), FAILED.as_dict(), "", "error", "error")
            except Exception:  # noqa: BLE001 - the dashboard times out by itself
                pass

    def _claim(self, rid: int) -> bool:
        response = self._session.patch(
            self._rest(), params={"id": f"eq.{rid}", "status": "eq.pending"},
            json={"status": "working"}, headers={"Prefer": "return=representation"},
            timeout=(CONNECT_TIMEOUT, READ_TIMEOUT))
        check_response(response, f"table {self.table}")
        return bool(response.json())

    def _reply(self, row: dict, body: str, texts: dict, photo: str, intent: str,
               status: str) -> None:
        """The door's answer, then the question's final status."""
        response = self._session.post(
            self._rest(), json={"user_id": row["user_id"], "sender": "door", "body": body[:4000],
                                "texts": texts, "photo": photo, "intent": intent[:40],
                                "status": "done", "reply_to": row["id"]},
            headers={"Prefer": "return=minimal"}, timeout=(CONNECT_TIMEOUT, READ_TIMEOUT))
        check_response(response, f"table {self.table}")
        response = self._session.patch(
            self._rest(), params={"id": f"eq.{row['id']}"},
            json={"status": status, "intent": intent[:40]}, headers={"Prefer": "return=minimal"},
            timeout=(CONNECT_TIMEOUT, READ_TIMEOUT))
        check_response(response, f"table {self.table}")

    def _photo(self, url: str) -> str:
        """A reply's picture ("/photos/<name>") uploaded to the bucket; its path there."""
        name = url.rsplit("/", 1)[-1]
        history = getattr(self.ctx, "history", None)
        path = history.photo_path(name) if (name and history is not None) else None
        if path is None or not path.exists():
            return ""
        target = cloud_photo_path(name)
        response = self._session.post(
            f"{self.cloud.url}/storage/v1/object/{quote(self.cloud.bucket)}/{quote(target)}",
            data=path.read_bytes(), headers={"Content-Type": "image/jpeg", "x-upsert": "true"},
            timeout=(CONNECT_TIMEOUT, READ_TIMEOUT))
        check_response(response, f"bucket {self.cloud.bucket}")
        return target

    # ---------------------------------------------------------------- helpers
    def _rest(self) -> str:
        return f"{self.cloud.url}/rest/v1/{quote(self.table)}"

    @staticmethod
    def _server_now(response) -> float:
        """Supabase's clock (the Date header); the Pi's own if it is missing."""
        try:
            return parsedate_to_datetime(response.headers["Date"]).timestamp()
        except (KeyError, TypeError, ValueError):
            return time.time()

    def _role(self, user: str) -> str:
        """The writer's role right now (door_members, read with the secret key)."""
        role, checked = self._roles.get(user, ("", 0.0))
        if time.monotonic() - checked < ROLE_CACHE_SECONDS:
            return role
        response = self._session.get(
            f"{self.cloud.url}/rest/v1/door_members",
            params={"select": "role", "user_id": f"eq.{user}"},
            timeout=(CONNECT_TIMEOUT, READ_TIMEOUT))
        check_response(response, "table door_members")
        rows = response.json()
        role = rows[0]["role"] if rows else ""
        self._roles[user] = (role, time.monotonic())
        return role

    def _sender(self, user: str) -> str:
        """"web:<name>" for the log, the AI's memory and the lock's reason:
        the part of the account's e-mail before the @."""
        if user not in self._names:
            name = user[:8]
            try:
                response = self._session.get(f"{self.cloud.url}/auth/v1/admin/users/{quote(user)}",
                                             timeout=(CONNECT_TIMEOUT, READ_TIMEOUT))
                if response.status_code < 400:
                    email = (response.json() or {}).get("email") or ""
                    name = email.split("@", 1)[0][:12] or name
            except Exception:  # noqa: BLE001 - the id is good enough
                pass
            self._names[user] = name
        return "web:" + self._names[user]

    def _retention(self) -> None:
        cutoff = datetime.fromtimestamp(time.time() - KEEP_DAYS * 86400, timezone.utc)
        response = self._session.delete(
            self._rest(), params={"created_at": f"lt.{cutoff.isoformat()}"},
            headers={"Prefer": "return=minimal"}, timeout=(CONNECT_TIMEOUT, READ_TIMEOUT))
        check_response(response, f"table {self.table}")
