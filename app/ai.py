"""Plain-language WhatsApp commands through the OpenAI API (optional).

The keyword router (app/commands.py) always answers first: exact, free and
offline.  Only a message it does not understand comes here, and only when
``OPENAI_API_KEY`` is in ``.env``.  The model never drives the door itself: it
asks for one of the router's own actions as a *tool* (open, lock, status,
photo, guest code, events, summary, doorbell answer), the router carries it
out with its usual checks, and the model words the answer in the owner's own
language and dialect.

Sent to OpenAI with such a message: the message, the last few lines of this
conversation (kept in memory only, ``[ai] memory_minutes``) and the door's
state -- lock, alarms, doorbell, camera and the latest events with names.
Never sent: photos, the stored door codes, any key.  Guest codes are blanked
out of the remembered conversation.
"""

from __future__ import annotations

import json
import re
import threading
import time

import requests

from . import history as H
from .config import env_value
from .wording import describe, mask_number

IGNORE = "<IGNORE>"
_CODE = re.compile(r"(?<!\d)\d{4,8}(?!\d)")
_ERROR_EVERY = 600.0       # self chat: one "AI is down" reply per 10 minutes


def _tool(name: str, description: str, properties: dict | None = None) -> dict:
    properties = properties or {}
    return {"type": "function", "function": {
        "name": name, "description": description, "strict": True,
        "parameters": {"type": "object", "properties": properties,
                       "required": list(properties), "additionalProperties": False}}}


TOOLS = [
    _tool("open_door",
          "Unlock the front door now; it locks again by itself after a few seconds. "
          "Only when the owner's latest message clearly asks to open the door right now. "
          "Never for a question, a negation (don't open), a condition (if Ahmed comes), "
          "a later time (at five, in an hour) or quoted words. If unsure, ask instead."),
    _tool("lock_door", "Lock the door at once (it also relocks by itself)."),
    _tool("get_status",
          "Current state: lock, alarms, doorbell, camera, fingerprint and card readers, "
          "active guest codes, and today's counts."),
    _tool("take_photo", "Take a picture with the door camera now; it is sent with your reply."),
    _tool("create_guest_code",
          "Create a one-time guest code for the door keypad (typed on the door, then #). "
          "Use it for someone who will come later or while the owner is away.",
          {"code": {"type": ["string", "null"],
                    "description": "4 to 8 digits the owner asked for, or null for a random one"},
           "minutes": {"type": ["integer", "null"],
                       "description": "How long it stays valid, in minutes (max 1440), "
                                      "or null for the default"}}),
    _tool("get_events",
          "The latest door events: entries (who and how), refused attempts, doorbell rings, alarms.",
          {"today": {"type": "boolean", "description": "true = only today's events"},
           "limit": {"type": "integer", "description": "How many, 1 to 15"}}),
    _tool("get_today_summary", "Today's counts: entries by method, refused attempts, rings, alarms."),
    _tool("answer_doorbell",
          "Answer the visitor who rang the doorbell and is waiting: open=true lets them in, "
          "open=false tells them no on the door screen.",
          {"open": {"type": "boolean"}}),
]

PROMPT = """\
You are the WhatsApp assistant of the smart door lock at the owner's home. You only ever talk with the owner.
Reply in the language and dialect of the owner's last message (usually Gulf Arabic), short like a WhatsApp chat: 1 to 4 lines, no headings or markdown, emojis welcome.
You act only through the tools. Never say something happened unless a tool result says so; if a tool reports a fault or refusal, say it plainly.
Door safety:
- open_door only when the latest message clearly asks to open the door now. Not for questions ("is it open?"), negations ("don't open"), conditions ("if Ahmed comes, open"), later times ("open at five"), or quoted words. When unsure, ask a short yes/no question and wait.
- You cannot schedule an opening: for a visitor who comes later, offer a one-time guest code (create_guest_code).
- While the doorbell is waiting, "open / yes / let them in" means answer_doorbell(open=true) and "no" means answer_doorbell(open=false).
- You never know the owners' own door codes; guest codes come only from create_guest_code.
- During a fire or smoke alarm the system keeps reopening the door on purpose, for safety.
{notes}
The door right now:
{state}"""

NOTES = ("This chat is also the owner's \"message yourself\" notes. If the message is clearly not "
         f"meant for you (a note, a list, a link to keep), reply with exactly {IGNORE} and nothing else.")


class Assistant:
    """``router`` is the :class:`app.commands.CommandRouter` that owns the tools."""

    def __init__(self, ctx, router):
        self.ctx = ctx
        self.router = router
        self._lock = threading.Lock()
        self._memory: dict[str, tuple[float, list[dict]]] = {}
        self._last_error = 0.0
        self._skip_effort = False
        self._session = requests.Session()
        if self.ready:
            ctx.events.info("ai.ready", f"OpenAI {self.model} answers what the keywords don't cover")
        elif self.enabled:
            ctx.events.info("ai.off", "OPENAI_API_KEY not set in .env; WhatsApp understands keyword commands only")

    # ------------------------------------------------------------- settings
    @property
    def enabled(self) -> bool:
        return self.ctx.config.getbool("ai", "enabled", True)

    @property
    def model(self) -> str:
        return self.ctx.config.get("ai", "model") or "gpt-6-luna"

    @staticmethod
    def _key() -> str:
        return env_value("OPENAI_API_KEY")   # read each time: no restart after adding it

    @property
    def ready(self) -> bool:
        return self.enabled and bool(self._key())

    # --------------------------------------------------------------- memory
    def _history(self, sender: str) -> list[dict]:
        keep = self.ctx.config.getfloat("ai", "memory_minutes", 15.0) * 60
        with self._lock:
            when, turns = self._memory.get(sender, (0.0, []))
            return list(turns) if time.time() - when <= keep else []

    def remember(self, sender: str, text: str, reply: str, intent: str) -> None:
        """One exchange, also the ones the keywords answered, so a follow-up
        such as "and lock it" makes sense to the model."""
        if not self.ready or not reply:
            return
        if "tempcode" in intent or "guest_code" in intent:
            text, reply = _CODE.sub("••••", text), _CODE.sub("••••", reply)
        size = max(0, self.ctx.config.getint("ai", "memory_messages", 8))
        turns = self._history(sender) + [{"role": "user", "content": text},
                                         {"role": "assistant", "content": reply}]
        with self._lock:
            self._memory[sender] = (time.time(), turns[-size:] if size else [])

    # ---------------------------------------------------------------- state
    def _state(self) -> str:
        ctx, router = self.ctx, self.router
        status = ctx.lock.status()
        if status.error:
            lock = "relay FAULT (the door may not open)"
        elif status.is_unlocked:
            lock = f"unlocked, locks again in {max(1, round(status.seconds_remaining))} s"
        else:
            lock = "locked"
        if ctx.lock.backend.simulated:
            lock += " (simulation mode: no relay, it cannot really open)"
        lines = [f"- time: {time.strftime('%A %d/%m/%Y %H:%M')}", f"- lock: {lock}"]
        alarms = router._alarms()
        lines.append("- alarm: " + (", ".join(alarms) + " alarm ACTIVE, door kept open for safety"
                                    if alarms else "none"))
        doorbell = getattr(ctx, "doorbell", None)
        left = doorbell.pending() if doorbell is not None else 0
        lines.append(f"- doorbell: a visitor is waiting for your answer ({round(left)} s left)"
                     if left > 0 else "- doorbell: nobody waiting")
        faces = ctx.faces
        camera = ("not set up" if faces is None or not faces.available
                  else "online" if faces.camera_online else "OFFLINE")
        lines.append(f"- camera: {camera}")
        active = ctx.temp_codes.active_count()
        if active:
            lines.append(f"- active guest codes: {active}")
        rows = ctx.history.recent(limit=6, categories=(H.ENTRY, H.DENIED, H.DOORBELL, H.ALARM))
        if rows:
            lines.append("- latest events (newest first):")
            lines += [f"  {time.strftime('%d/%m %H:%M', time.localtime(r['ts']))} {describe(r, True)}"
                      for r in rows]
        return "\n".join(lines)

    # ---------------------------------------------------------------- tools
    def _run(self, name: str, args: dict, ar: bool, sender: str, done: set) -> dict:
        router = self.router
        doorbell = getattr(self.ctx, "doorbell", None)
        waiting = doorbell is not None and doorbell.pending() > 0
        if name == "open_door":
            if "open_door" in done:
                return router._result("open", "Already opened for this message.")
            return router._do_bell_yes([], ar, sender) if waiting else router._do_open([], ar, sender)
        if name == "lock_door":
            return router._do_lock([], ar, sender)
        if name == "get_status":
            return router._do_status([], ar, sender)
        if name == "take_photo":
            return router._do_photo([], ar, sender)
        if name == "create_guest_code":
            code = args.get("code")
            if code is not None:
                code = re.sub(r"\D", "", str(code))
                if not 4 <= len(code) <= 8:
                    return router._result("tempcode", "Refused: a guest code is 4 to 8 digits.")
            minutes = args.get("minutes")
            usable = isinstance(minutes, int) and not isinstance(minutes, bool) and minutes > 0
            seconds = minutes * 60 if usable else None
            return router._issue_tempcode(code or None, seconds, ar)
        if name == "get_events":
            limit = args.get("limit")
            limit = limit if isinstance(limit, int) and not isinstance(limit, bool) else 8
            return router._events(bool(args.get("today")), limit, ar)
        if name == "get_today_summary":
            return router._do_summary([], ar, sender)
        if name == "answer_doorbell":
            if not waiting:
                return router._result("no_bell", "Nobody is waiting at the doorbell now.")
            return (router._do_bell_yes if args.get("open") else router._do_bell_no)([], ar, sender)
        return router._result("unknown", f"Unknown tool {name}.")

    # ------------------------------------------------------------------ API
    def _call(self, messages: list[dict]) -> dict:
        cfg = self.ctx.config
        base = (cfg.get("ai", "base_url") or "https://api.openai.com/v1").rstrip("/")
        payload = {"model": self.model, "messages": messages, "tools": TOOLS,
                   "max_completion_tokens": cfg.getint("ai", "max_tokens", 800)}
        effort = cfg.get("ai", "reasoning_effort").strip()
        if effort and not self._skip_effort:
            payload["reasoning_effort"] = effort
        res = self._session.post(f"{base}/chat/completions", json=payload,
                                 headers={"Authorization": f"Bearer {self._key()}"},
                                 timeout=(10, cfg.getfloat("ai", "timeout_seconds", 40.0)))
        if res.status_code == 400 and "reasoning_effort" in res.text and "reasoning_effort" in payload:
            # This model takes no reasoning setting: drop it from now on.
            self._skip_effort = True
            self.ctx.events.warning("ai.config", f"{self.model} refuses reasoning_effort={effort}; sent without it")
            return self._call(messages)
        if res.status_code != 200:
            raise ApiError(res)
        return res.json()

    def _error_reply(self, exc: Exception, ar: bool) -> str:
        if isinstance(exc, ApiError):
            reason = {
                "invalid_api_key": ("مفتاح OpenAI غير صحيح (OPENAI_API_KEY في .env)",
                                    "the OpenAI key is wrong (OPENAI_API_KEY in .env)"),
                "insufficient_quota": ("رصيد OpenAI خلص", "the OpenAI credit has run out"),
                "model_not_found": (f"الموديل {self.model} غير متاح؛ غيّره في config.ini [ai] model",
                                    f"model {self.model} is not available; change [ai] model in config.ini"),
                "rate_limit": ("OpenAI مشغول، جرّب بعد دقيقة", "OpenAI is busy, try again in a minute"),
            }.get(exc.kind, (f"OpenAI رفض الطلب ({exc.status})", f"OpenAI refused ({exc.status})"))
        else:
            reason = ("ما قدرت أوصل لـ OpenAI الحين", "could not reach OpenAI right now")
        return self.router._t(
            ar, f"⚠️ {reason[0]}.\nالأوامر الأساسية شغّالة بدونه: اكتب «مساعدة».",
            f"⚠️ {reason[1]}.\nThe basic commands still work: send \"help\".")

    # ---------------------------------------------------------------- entry
    def answer(self, text: str, sender: str, ar: bool, self_chat: bool, fallback: dict) -> dict:
        """The model's answer to a message the keywords did not understand."""
        router, events = self.router, self.ctx.events
        system = PROMPT.format(notes=NOTES if self_chat else "", state=self._state())
        messages = [{"role": "system", "content": system}, *self._history(sender),
                    {"role": "user", "content": text}]
        done: list[str] = []
        photo = ""
        last_reply = ""
        reply = ""
        tokens = 0
        try:
            for _ in range(max(1, self.ctx.config.getint("ai", "max_rounds", 4))):
                data = self._call(messages)
                tokens += int((data.get("usage") or {}).get("total_tokens") or 0)
                message = (data.get("choices") or [{}])[0].get("message") or {}
                calls = message.get("tool_calls") or []
                if not calls:
                    reply = (message.get("content") or "").strip()
                    break
                messages.append({"role": "assistant", "content": message.get("content"),
                                 "tool_calls": calls})
                for call in calls:
                    name = (call.get("function") or {}).get("name", "")
                    try:
                        args = json.loads((call.get("function") or {}).get("arguments") or "{}")
                    except ValueError:
                        args = {}
                    result = self._run(name, args if isinstance(args, dict) else {}, ar, sender, set(done))
                    done.append(name)
                    photo = result.get("photo") or photo
                    last_reply = result.get("reply") or last_reply
                    content = result.get("reply") or ""
                    if result.get("photo"):
                        content += "\n(The picture is attached to your reply.)"
                    messages.append({"role": "tool", "tool_call_id": call.get("id", ""), "content": content})
        except (requests.RequestException, ValueError, ApiError) as exc:
            detail = exc.detail if isinstance(exc, ApiError) else type(exc).__name__
            events.warning("ai.error", f"{self.model}: {detail}")
            if done:     # something already happened: say what, even without the model
                return router._result("ai:" + ",".join(done), last_reply, photo=_name(photo))
            if self_chat and time.time() - self._last_error < _ERROR_EVERY:
                return fallback
            self._last_error = time.time()
            return router._result("ai_error", self._error_reply(exc, ar))

        intent = "ai:" + ",".join(done) if done else "ai"
        if reply.strip() == IGNORE or (not reply and not done):
            if not done:
                return router._result("ai_ignore", "", handled=False)
            reply = ""
        reply = reply.replace(IGNORE, "").strip() or last_reply
        events.info("ai.answer", f"{intent} for {mask_number(sender)} ({tokens} tokens)")
        result = router._result(intent, reply, photo=_name(photo))
        self.remember(sender, text, reply, intent)
        return result


def _name(route: str | None) -> str:
    """``/photos/x.jpg`` -> ``x.jpg`` (router._result adds the prefix back)."""
    return (route or "").rsplit("/", 1)[-1]


class ApiError(Exception):
    """A non-200 answer from the API, with OpenAI's own error code."""

    def __init__(self, res: requests.Response):
        self.status = res.status_code
        try:
            err = (res.json() or {}).get("error") or {}
        except ValueError:
            err = {}
        code = str(err.get("code") or err.get("type") or "")
        if code == "insufficient_quota":
            self.kind = code
        elif res.status_code == 429:
            self.kind = "rate_limit"
        elif res.status_code == 401 or code == "invalid_api_key":
            self.kind = "invalid_api_key"
        elif res.status_code == 404 or code == "model_not_found":
            self.kind = "model_not_found"
        else:
            self.kind = code or str(res.status_code)
        self.detail = f"HTTP {res.status_code} {code} {str(err.get('message') or '')[:160]}".strip()
        super().__init__(self.detail)
