"""HTTP API for the WhatsApp bot, reached through the reverse SSH tunnel
(deploy/door-tunnel.service).

    POST /command   {"text": "افتح الباب", "from": "9689..."}
                                                      -> {"ok": true, "handled": true,
                                                          "intent": "open", "reply": "...",
                                                          "photo": null}
    GET  /alerts                                      -> {"ok": true, "alerts": [...]}
    POST /alerts/ack {"id": 7}                        -> {"ok": true, "removed": 2}
    POST /alerts/test                                 -> {"ok": true, "id": 8}
    GET  /photos/<name>.jpg                           -> the JPEG
    GET  /photos/<name>.jpg/url                       -> {"ok": true, "url": "https://..."}
    GET  /status                                      -> lock, alarms, camera, today ...
    GET  /events?limit=20&category=entry              -> {"ok": true, "events": [...]}
    POST /openDoor                                    -> {"ok": true}
    POST /tempCode  {"code": "1122", "seconds": 60}   -> {"ok": true, "expires_in": 60}

Two loops make up the bot's side, and neither needs an AI model:

* every incoming WhatsApp message goes to ``/command`` (app/commands.py),
  which understands the door commands by keyword and returns the reply;
* the bot polls ``/alerts`` (fire, smoke, wrong code, doorbell: see
  app/alerts.py), sends each one's ``message`` -- with the picture when there
  is a ``photo`` -- then acknowledges the highest id it sent.

``/status`` and ``/events`` are there for a bot that *does* have a model and
wants facts to answer a free-form question with.

It runs as a thread inside the lock app rather than as its own program: the
app owns the relay pin for its whole life, so a second process could not drive
GPIO17.  The door is opened with the same ``LockController.unlock`` the keypad
uses.

Security:

* Binds to 127.0.0.1 only; the bot reaches it solely through the tunnel.
* Every request, to any path, must carry ``X-Door-Key`` equal to ``DOOR_KEY``
  from ``.env`` (compared with :func:`hmac.compare_digest`).  Anything else
  gets a bare 401 and an ``api.auth`` line in the event log.
* Without a usable ``DOOR_KEY`` the API does not start at all.
"""

from __future__ import annotations

import hmac
import threading
import time
from pathlib import Path

from flask import Flask, jsonify, request, send_file
from werkzeug.exceptions import HTTPException
from werkzeug.serving import WSGIRequestHandler, make_server

from .config import ENV_PATH, env_value
from .history import CATEGORIES, public_row, start_of_day

HOST = "127.0.0.1"  # never 0.0.0.0: the tunnel is the only way in
MIN_KEY_LENGTH = 32
MAX_BODY = 4096     # a WhatsApp text in UTF-8, with room to spare


def load_door_key(path: Path = ENV_PATH) -> str:
    """``DOOR_KEY`` from the environment, else from the ``.env`` file."""
    return env_value("DOOR_KEY", path)


def _clean(text: str, limit: int = 80) -> str:
    """Make client-supplied text safe for a one-line log entry."""
    return "".join(ch if ch.isprintable() else "?" for ch in text[:limit])


class _AuthFailures:
    """Logs refused requests, at most ``LIMIT`` lines a minute.

    The Funnel hostname is public (it appears in certificate-transparency
    logs), so scanners will knock.  Past the limit, refusals are only counted
    and summed up in one line, so a flood cannot rotate the door's history out
    of the event log.
    """

    LIMIT, WINDOW = 20, 60.0

    def __init__(self, events):
        self.events = events
        self._lock = threading.Lock()
        self._window_start = 0.0
        self._count = 0
        self._suppressed = 0

    def record(self, detail: str) -> None:
        with self._lock:
            now = time.monotonic()
            summary = None
            if now - self._window_start >= self.WINDOW:
                if self._suppressed:
                    summary = (f"{self._suppressed} more refused request(s) "
                               f"not logged individually")
                self._window_start, self._count, self._suppressed = now, 0, 0
            self._count += 1
            log_it = self._count <= self.LIMIT
            if not log_it:
                self._suppressed += 1
        if summary:
            self.events.warning("api.auth", summary)
        if log_it:
            self.events.warning("api.auth", detail)


class _Handler(WSGIRequestHandler):
    def version_string(self) -> str:
        return "door"  # do not advertise Werkzeug/Python versions


def create_app(ctx, door_key: str) -> Flask:
    app = Flask(__name__)
    app.config["MAX_CONTENT_LENGTH"] = MAX_BODY
    expected = door_key.encode("utf-8")
    events = ctx.events
    refused = _AuthFailures(events)

    @app.before_request
    def require_key():
        given = request.headers.get("X-Door-Key")
        if given is not None and hmac.compare_digest(given.encode("utf-8"), expected):
            return None
        origin = request.headers.get("X-Forwarded-For") or request.remote_addr or "?"
        refused.record(f"refused {_clean(request.method, 10)} {_clean(request.path)} "
                       f"from {_clean(origin, 60)} "
                       f"({'no key' if given is None else 'wrong key'})")
        return jsonify(ok=False), 401

    @app.post("/openDoor")
    def open_door():
        lock = ctx.lock
        if lock.backend.simulated:
            events.warning("api.open", "refused: relay is simulated, the door cannot open")
            return jsonify(ok=False, error="relay unavailable"), 503
        if not lock.unlock(reason="whatsapp"):
            return jsonify(ok=False, error="relay fault"), 500
        return jsonify(ok=True)

    @app.post("/tempCode")
    def temp_code():
        body = request.get_json(force=True, silent=True)
        if not isinstance(body, dict):
            return jsonify(ok=False, error="body must be a JSON object"), 400
        try:
            expires_in = ctx.temp_codes.add(body.get("code"), body.get("seconds"))
        except ValueError as exc:
            return jsonify(ok=False, error=str(exc)), 400
        return jsonify(ok=True, expires_in=expires_in)

    @app.post("/command")
    def command():
        """One WhatsApp message in, the answer out (app/commands.py)."""
        body = request.get_json(force=True, silent=True)
        text = body.get("text") if isinstance(body, dict) else None
        if not isinstance(text, str):
            return jsonify(ok=False, error='body must be {"text": "...", "from": "..."}'), 400
        sender = body.get("from")
        result = ctx.commands.handle(text[:1000], sender if isinstance(sender, str) else "")
        return jsonify(ok=True, **result)

    @app.get("/photos/<name>")
    def photo(name: str):
        """A picture named by an alert or a command reply."""
        path = ctx.history.photo_path(name)
        if path is None:
            return jsonify(ok=False, error="no such photo"), 404
        return send_file(path, mimetype="image/jpeg", max_age=0)

    @app.get("/photos/<name>/url")
    def photo_url(name: str):
        """A temporary public link to the picture, for a WhatsApp provider that
        takes a URL instead of the file.  Needs the Supabase sync."""
        if ctx.history.photo_path(name) is None:
            return jsonify(ok=False, error="no such photo"), 404
        cloud = ctx.cloud
        url = cloud.signed_url(name) if cloud is not None else None
        if not url:
            return jsonify(ok=False, error="not in the cloud (yet)"), 503
        return jsonify(ok=True, url=url)

    @app.get("/status")
    def status():
        """The door right now, for a bot that wants facts rather than text."""
        lock = ctx.lock.status()
        summary = ctx.history.summary(start_of_day())
        doorbell = ctx.doorbell
        return jsonify(
            ok=True,
            lock="unlocked" if lock.is_unlocked else "locked",
            seconds_remaining=round(lock.seconds_remaining, 1),
            relay_fault=lock.error,
            simulated=lock.simulated,
            alarms=ctx.hazards.alarms() if ctx.hazards is not None else [],
            camera=bool(ctx.faces.available and ctx.faces.camera_online),
            doorbell_pending=round(doorbell.pending()) if doorbell is not None else 0,
            temp_codes=ctx.temp_codes.active_count(),
            cloud=ctx.cloud.state() if ctx.cloud is not None else "off",
            today={"entries": sum(summary["entries"].values()),
                   "denied": sum(summary["denied"].values()),
                   "rings": summary["rings"], "alarms": summary["alarms"]},
            last_entry=public_row(summary["last_entry"]) if summary["last_entry"] else None,
        )

    @app.get("/events")
    def events_list():
        """Recent history rows, newest first."""
        try:
            limit = max(1, min(int(request.args.get("limit", 20)), 200))
        except ValueError:
            return jsonify(ok=False, error="limit must be a number"), 400
        category = request.args.get("category")
        if category is not None and category not in CATEGORIES:
            return jsonify(ok=False, error="category: " + ", ".join(CATEGORIES)), 400
        rows = ctx.history.recent(limit, [category] if category else None,
                                  start_of_day() if request.args.get("today") else None)
        return jsonify(ok=True, events=[public_row(r) for r in rows])

    @app.get("/alerts")
    def alerts_list():
        """Alerts the bot has not acknowledged yet, oldest first.  The poll
        itself tells the door that the bot is alive."""
        ctx.alerts.polled()
        return jsonify(ok=True, alerts=ctx.alerts.pending())

    @app.post("/alerts/ack")
    def alerts_ack():
        body = request.get_json(force=True, silent=True)
        up_to = body.get("id") if isinstance(body, dict) else None
        if not isinstance(up_to, int) or isinstance(up_to, bool) or up_to < 1:
            return jsonify(ok=False, error='body must be {"id": <alert id>}'), 400
        return jsonify(ok=True, removed=ctx.alerts.ack(up_to))

    @app.post("/alerts/test")
    def alerts_test():
        """Queue a harmless test alert, to check the bot end to end."""
        alert_id = ctx.alerts.add("test", "test",
                                  "🧪 رسالة تجربة من القفل الذكي: التنبيهات تصل للواتساب.")
        return jsonify(ok=True, id=alert_id)

    @app.errorhandler(HTTPException)
    def http_error(exc: HTTPException):
        return jsonify(ok=False), exc.code

    @app.errorhandler(Exception)
    def crash(exc: Exception):
        events.error("api.exception", f"{type(exc).__name__}: {exc}")
        return jsonify(ok=False), 500

    return app


class ApiServer:
    """Serves :func:`create_app` on 127.0.0.1 from a daemon thread.

    If the port is busy (say, a second copy of the app is running), it keeps
    retrying instead of dying, so the API comes back on its own.
    """

    RETRY_SECONDS = 5.0

    def __init__(self, ctx):
        self.ctx = ctx
        self.port = ctx.config.getint("api", "port", 8000)
        self._stop = threading.Event()
        self._lock = threading.Lock()
        self._server = None
        self._thread: threading.Thread | None = None

    def start(self) -> bool:
        events = self.ctx.events
        key = load_door_key()
        if len(key) < MIN_KEY_LENGTH:
            events.error("api.start", f"DOOR_KEY missing or shorter than {MIN_KEY_LENGTH} "
                                      f"characters in {ENV_PATH.name}; API disabled")
            return False
        app = create_app(self.ctx, key)
        self._thread = threading.Thread(target=self._run, args=(app,), name="api",
                                        daemon=True)
        self._thread.start()
        return True

    def _run(self, app: Flask) -> None:
        events = self.ctx.events
        reported = False
        while not self._stop.is_set():
            try:
                server = make_server(HOST, self.port, app, threaded=True,
                                     request_handler=_Handler)
            # Werkzeug reports a busy port with sys.exit(1), not by raising OSError.
            except (OSError, SystemExit) as exc:
                if not reported:
                    reason = exc if isinstance(exc, OSError) else "address in use?"
                    events.error("api.start", f"cannot listen on {HOST}:{self.port}: "
                                              f"{reason}; retrying every "
                                              f"{self.RETRY_SECONDS:.0f}s")
                    reported = True
                self._stop.wait(self.RETRY_SECONDS)
                continue
            with self._lock:
                if self._stop.is_set():
                    server.server_close()
                    return
                self._server = server
            events.info("api.start", f"listening on http://{HOST}:{self.port}")
            server.serve_forever()
            server.server_close()
            return

    def close(self) -> None:
        with self._lock:
            self._stop.set()
            server = self._server
        if server is not None:
            server.shutdown()
