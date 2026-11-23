"""Turns the event log into history rows, camera pictures and WhatsApp alerts.

Everything the door does already goes through :class:`app.events.EventLog`.
The recorder subscribes to it, and for each event that matters:

1. takes a picture from the door camera (entries, refusals, doorbell, alarms);
2. writes a structured row to ``data/history.db`` (app/history.py), which the
   cloud sync then copies to Supabase for the dashboard;
3. queues a WhatsApp alert for the bot when ``[notify]`` says so: wrong door
   code with the person's picture, the doorbell question, an unknown finger...

The work happens on its own thread: the screen that logged the event never
waits for the camera or the disk.

:func:`classify` holds the whole policy (which event is what, and what the
owner is told), as a pure function so it can be tested without hardware.
"""

from __future__ import annotations

import queue
import re
import threading
import time
from dataclasses import dataclass

from . import history as H
from .config import Config
from .wording import ar_duration, clock_line

PHOTO_REUSE = 2.0          # a picture this fresh is reused instead of a new one
FAULT_GAP = 1800.0         # the same fault is reported at most this often
CLEANUP_EVERY = 6 * 3600.0

_UNLOCK = re.compile(r"^[\d.]+s(?: \((.*)\))?$")
_FACE = re.compile(r"^face (.+) \d\.\d\d$")
_FINGER = re.compile(r"^fingerprint (.+) slot \d+$")
_CARD = re.compile(r"^card (.+)$")
_MENU = re.compile(r"^menu \((.+)\)$")
_ALARM_OPEN = re.compile(r"^((?:fire|smoke)(?: \+ (?:fire|smoke))*) alarm(?:, re-open \d+)?$")
_SECONDS = re.compile(r"(\d+)\s*s\b")
_LEFT = re.compile(r"\((\d+) attempt")


@dataclass
class Record:
    """What to do about one event."""

    category: str
    method: str = ""
    person: str = ""
    photo: str = ""           # tag for the picture's file name; "" = no picture
    alert: str = ""           # alert type for the bot; "" = no WhatsApp alert
    alert_event: str = ""
    text: str = ""            # the alert, in Arabic, without the time line
    setting: str = ""         # [notify] option that must be on; "" = always
    urgent: bool = False      # not held back by [notify] min_gap_seconds
    gap: float | None = None  # own minimum gap, in place of min_gap_seconds
    ask: bool = False         # the owner is expected to answer
    needs_photo: bool = False  # the alert is pointless without its picture


def _unlock(reason: str) -> Record | None:
    if reason == "keypad":
        return Record(H.ENTRY, "keypad", photo="keypad", alert="entry",
                      alert_event="opened", setting="entries",
                      text="🚪 انفتح الباب برمز الباب.")
    if reason == "temp code":
        return Record(H.ENTRY, "temp_code", photo="tempcode", alert="entry",
                      alert_event="guest", setting="temp_code_used",
                      text="🔑 الضيف استخدم الرمز المؤقت ودخل.")
    m = _FACE.match(reason)
    if m:
        return Record(H.ENTRY, "face", m.group(1), photo="face", alert="entry",
                      alert_event="opened", setting="entries",
                      text=f"🚪 {m.group(1)} دخل (بالوجه).")
    m = _FINGER.match(reason)
    if m:
        return Record(H.ENTRY, "fingerprint", m.group(1), photo="finger", alert="entry",
                      alert_event="opened", setting="entries",
                      text=f"🚪 {m.group(1)} دخل (بالبصمة).")
    m = _CARD.match(reason)
    if m:
        return Record(H.ENTRY, "card", m.group(1), photo="card", alert="entry",
                      alert_event="opened", setting="entries",
                      text=f"🚪 {m.group(1)} دخل (بالكرت).")
    m = _MENU.match(reason)
    if m:
        return Record(H.ENTRY, "menu", m.group(1), photo="menu", alert="entry",
                      alert_event="opened", setting="entries",
                      text=f"🚪 {m.group(1)} فتح الباب من لوحة التحكم.")
    if reason == "whatsapp doorbell":
        return Record(H.ENTRY, "doorbell", photo="bell_open", alert="entry",
                      alert_event="visitor", setting="entries",
                      text="🚪 الزائر دخل بعد موافقتك على الجرس.")
    if reason.startswith("whatsapp"):
        return Record(H.ENTRY, "whatsapp", photo="whatsapp", alert="entry",
                      alert_event="remote", setting="entries",
                      text="🚪 انفتح الباب بأمر واتساب. هذي الصورة من عند الباب.")
    m = _ALARM_OPEN.match(reason)
    if m:
        return Record(H.ALARM, m.group(1).split(" + ")[0])
    if reason == "self-test":
        return Record(H.SYSTEM, "test", photo="test", alert="admin", alert_event="relay_test",
                      setting="admin_changes",
                      text="🔧 تجربة الريلاي من لوحة التحكم: الباب انفتح ثانية وحدة.")
    if reason == "simulation button":
        return Record(H.SYSTEM, "test")
    return Record(H.ENTRY, "other", photo="entry", alert="entry", alert_event="opened",
                  setting="entries", text="🚪 انفتح الباب.")


def _who(message: str) -> str:
    """The person a "Name (details)" admin message is about."""
    return message.split(" (")[0]


def _admin_change(event: str, message: str) -> Record | None:
    """A change made on the door panel: told to the owner with a picture of
    whoever stands at the panel ([notify] admin_changes)."""
    text = None
    person = ""
    if event == "passcode.changed":
        text = "🔐 تم تغيير رمز الباب من لوحة التحكم."
    elif event == "user.add":
        person = _who(message)
        text = f"👤 مستخدم جديد أُضيف من لوحة الباب: {message}"
    elif event == "user.delete":
        person = _who(message)
        text = f"🗑 مستخدم انحذف من لوحة الباب: {message}"
    elif event == "user.enabled":
        m = re.match(r"^(.+) -> (enabled|disabled)$", message)
        person = m.group(1) if m else ""
        state = "تفعيل" if m and m.group(2) == "enabled" else "تعطيل"
        text = f"👤 تم {state} حساب {person or 'مستخدم'} من لوحة الباب."
    elif event == "user.pin":
        m = re.match(r"^PIN changed for (.+)$", message)
        person = m.group(1) if m else ""
        text = f"🔑 تغيّر PIN حساب {person or 'مستخدم'} على لوحة الباب."
    elif event == "face.add":
        person = _who(message)
        text = f"🙂 وجه جديد يفتح الباب أُضيف لـ {person}."
    elif event == "face.delete":
        person = _who(message)
        text = f"🗑 انحذف وجه من حساب {person}."
    elif event == "fingerprint.add":
        m = re.match(r"^slot \d+ -> (.+?) '", message)
        person = m.group(1) if m else ""
        text = f"👆 بصمة جديدة تفتح الباب أُضيفت لـ {person or 'مستخدم'}."
    elif event == "fingerprint.delete":
        m = re.match(r"^slot \d+ \((.+)\)$", message)
        person = m.group(1) if m else ""
        text = f"🗑 انحذفت بصمة من حساب {person or 'مستخدم'}."
    elif event == "fingerprint.clear":
        text = "🗑 انمسحت كل البصمات من حساس البصمة."
    elif event == "card.add":
        person = _who(message)
        text = f"💳 كرت جديد يفتح الباب أُضيف لـ {person}."
    elif event == "card.delete":
        person = _who(message)
        text = f"🗑 انحذف كرت من حساب {person}."
    elif event == "security.reset":
        m = re.match(r"^lockouts cleared by (.+)$", message)
        person = m.group(1) if m else ""
        text = f"🔓 {person or 'المدير'} مسح حظر المحاولات من لوحة الباب."
    elif event == "app.exit":
        m = re.match(r"^closed by (.+)$", message)
        person = m.group(1) if m else ""
        return Record(H.ADMIN, person=person, photo="admin", alert="admin",
                      alert_event="exit", setting="admin_changes", urgent=True,
                      text=f"⛔ {person or 'المدير'} أغلق برنامج الباب من اللوحة.\n"
                           "الأرقام والوجه والبصمة والكرت ما تفتح الباب لين يشتغل من جديد.")
    if text is None:
        return None
    return Record(H.ADMIN, person=person, photo="admin", alert="admin",
                  alert_event=event.replace(".", "_"), setting="admin_changes", text=text)


def classify(event: str, message: str, level: str = "INFO",
             answer_seconds: float = 120.0) -> Record | None:
    """The record for one event-log line, or None when it is not history."""
    if event == "lock.unlock":
        m = _UNLOCK.match(message)
        return _unlock((m.group(1) or "") if m else message)

    # ------------------------------------------------------------- refusals
    if event == "keypad.deny":
        if "keypad locked" in message:
            return None  # keypad.lockout, logged just before, is the record
        if "new administrator" in message:
            return Record(H.DENIED, "keypad", photo="keypad_deny", alert="keypad",
                          alert_event="denied", setting="wrong_code",
                          text="⚠️ أحدهم حاول إنشاء حساب مدير على لوحة الباب برمز خاطئ.")
        m = _LEFT.search(message)
        left = f"\nالمحاولات الباقية قبل قفل اللوحة: {m.group(1)}" if m else ""
        return Record(H.DENIED, "keypad", photo="keypad_deny", alert="keypad",
                      alert_event="denied", setting="wrong_code",
                      text="⚠️ رمز خاطئ على لوحة الباب." + left)
    if event == "keypad.lockout":
        m = _SECONDS.search(message)
        wait = ar_duration(int(m.group(1))) if m else "مؤقتًا"
        return Record(H.DENIED, "keypad", photo="keypad_lock", alert="keypad",
                      alert_event="lockout", setting="wrong_code", urgent=True,
                      text=f"🚫 محاولات خاطئة متكررة لرمز الباب.\nلوحة الأرقام مقفلة {wait}.")
    if event == "fingerprint.deny":
        m = re.match(r"^(.+) is disabled", message)
        if m:
            return Record(H.DENIED, "fingerprint", m.group(1), photo="finger_deny",
                          alert="fingerprint", alert_event="denied",
                          text=f"⚠️ {m.group(1)} حاول يفتح الباب ببصمته، وحسابه معطّل.")
        return Record(H.DENIED, "fingerprint", photo="finger_deny", alert="fingerprint",
                      alert_event="denied", setting="unknown_fingerprint",
                      text="⚠️ بصمة غير مسجّلة حاولت فتح الباب.")
    if event == "card.deny":
        m = re.match(r"^(.+) is disabled", message)
        if m:
            return Record(H.DENIED, "card", m.group(1), photo="card_deny", alert="card",
                          alert_event="denied",
                          text=f"⚠️ {m.group(1)} حاول يفتح الباب بكرته، وحسابه معطّل.")
        m = _LEFT.search(message)
        left = f"\nالمحاولات الباقية قبل قفل قارئ الكروت: {m.group(1)}" if m else ""
        return Record(H.DENIED, "card", photo="card_deny", alert="card",
                      alert_event="denied", setting="unknown_card",
                      text="⚠️ كرت غير مسجّل حاول يفتح الباب." + left)
    if event == "card.lockout":
        m = _SECONDS.search(message)
        wait = ar_duration(int(m.group(1))) if m else "مؤقتًا"
        return Record(H.DENIED, "card", photo="card_lock", alert="card",
                      alert_event="lockout", setting="unknown_card", urgent=True,
                      text=f"🚫 كروت غير مسجّلة تكررت على قارئ الباب.\nقارئ الكروت مقفل {wait}.")
    if event == "face.deny":
        m = re.match(r"^(.+) is disabled$", message)
        if m:
            return Record(H.DENIED, "face", m.group(1), photo="face_deny", alert="face",
                          alert_event="denied",
                          text=f"⚠️ {m.group(1)} حاول يدخل بوجهه، وحسابه معطّل.")
        if message.startswith("unknown face"):
            return Record(H.DENIED, "face", photo="face_deny", alert="face",
                          alert_event="unknown", setting="unknown_face",
                          needs_photo=True, text="👤 شخص غير معروف واقف عند الباب.")
        return Record(H.DENIED, "face")
    if event == "login.deny":
        m = re.match(r"^wrong PIN for (.+)$", message)
        who = m.group(1) if m else ""
        return Record(H.DENIED, "login", who, photo="pin_deny", alert="login",
                      alert_event="denied", setting="wrong_pin",
                      text="⚠️ PIN خاطئ عند محاولة الدخول"
                           + (f" بحساب {who}" if who else "") + " على لوحة الباب.")
    if event == "login.lockout":
        m = _SECONDS.search(message)
        wait = ar_duration(int(m.group(1))) if m else "مؤقتًا"
        return Record(H.DENIED, "login", photo="pin_lock", alert="login",
                      alert_event="lockout", setting="wrong_pin", urgent=True,
                      text=f"🚫 محاولات PIN خاطئة متكررة.\nتسجيل الدخول على اللوحة مقفل {wait}.")
    if event == "bot.deny":
        return Record(H.DENIED, "whatsapp", alert="bot", alert_event="denied", gap=60.0,
                      text="🚫 رقم غير مصرّح له حاول يرسل أمرًا للباب:\n" + message)

    # -------------------------------------------------------------- doorbell
    if event == "doorbell.ring":
        again = "again" in message
        return Record(
            H.DOORBELL, "doorbell", photo="bell", alert="doorbell", alert_event="ring",
            urgent=True, ask=True,
            text=("🔔 رنّ الجرس مرة ثانية، ما زال ينتظر عند الباب." if again
                  else "🔔 في أحد عند الباب!")
                 + "\nتفتح له؟ رد بـ «نعم» أو «لا»."
                 + f"\n(الرد مقبول لمدة {ar_duration(answer_seconds)})")
    if event in ("doorbell.answer", "doorbell.timeout"):
        return Record(H.DOORBELL, "doorbell")

    # ----------------------------------------------------------------- alarms
    if event in ("hazard.fire", "hazard.smoke"):
        kind = event.split(".")[1]
        if "DETECTED" in message:
            what = "الحريق" if kind == "fire" else "الدخان"
            return Record(H.ALARM, kind, photo=kind, alert=kind, alert_event="photo",
                          setting="alarm_photo", urgent=True, needs_photo=True,
                          text=f"📷 صورة من كاميرا الباب لحظة إنذار {what}.")
        if "alarm over" in message:
            return Record(H.ALARM, kind)
        return Record(H.SYSTEM, kind) if level != "INFO" else None
    if event == "hazard.door":
        return Record(H.ALARM)
    if event == "hazard.exception":
        return Record(H.SYSTEM)

    # ---------------------------------------------------------- remote, admin
    if event == "bot.command":
        return Record(H.REMOTE, "whatsapp")
    if event == "tempcode.add":
        return Record(H.REMOTE, "temp_code")
    if event == "session.login":
        who = message.split(" (")[0]
        return Record(H.ADMIN, "login", who, photo="admin", alert="admin",
                      alert_event="login", setting="panel_login",
                      text=f"👤 {who} سجّل دخول على لوحة الباب.")
    admin = _admin_change(event, message)
    if admin is not None:
        return admin
    if event in ("fingerprint.enroll", "fingerprint.erase"):
        return Record(H.ADMIN)   # the same change as fingerprint.add / .delete

    # ----------------------------------------------------------------- system
    if event == "app.start":
        return Record(H.SYSTEM, alert="system", alert_event="start", setting="startup",
                      text="🔌 نظام الباب اشتغل (تشغيل جديد أو بعد انقطاع الكهرباء).")
    if event == "lock.shutdown":
        return Record(H.SYSTEM)
    if event == "face.camera":
        if message.endswith("offline"):
            return Record(H.SYSTEM, "camera", alert="camera", alert_event="offline",
                          setting="faults", gap=FAULT_GAP,
                          text="📷 كاميرا الباب انفصلت أو تعطلت.\n"
                               "التنبيهات القادمة بدون صور لين ترجع.")
        return Record(H.SYSTEM, "camera", alert="camera", alert_event="online",
                      setting="faults", text="✅ كاميرا الباب رجعت تشتغل.")
    if event == "lock.io":
        return Record(H.SYSTEM, "relay", alert="relay", alert_event="fault",
                      setting="faults", gap=FAULT_GAP,
                      text="⚠️ عطل في ريلاي القفل: الباب ربما ما ينفتح.\n" + message)
    if event == "card.offline":
        return Record(H.SYSTEM, "card", alert="card", alert_event="offline",
                      setting="faults", gap=FAULT_GAP,
                      text="💳 قارئ الكروت لا يرد: الكروت ما تفتح الباب لين يرجع.")
    if event == "card.online":
        return Record(H.SYSTEM, "card")
    if event == "fingerprint.offline":
        return Record(H.SYSTEM, "fingerprint", alert="fingerprint", alert_event="offline",
                      setting="faults", gap=FAULT_GAP,
                      text="👆 حساس البصمة لا يرد: البصمة ما تفتح الباب لين يرجع.")
    if event in ("fingerprint.offline", "fingerprint.online", "fingerprint.fault",
                 "fingerprint.error", "face.error", "ui.exception", "api.exception",
                 "api.auth"):
        return Record(H.SYSTEM)
    return None


class Recorder:
    """Subscribes on construction, so nothing logged before :meth:`start` is
    missed; the worker catches up once the camera and the alert queue exist."""

    def __init__(self, config: Config, events, history):
        self.config = config
        self.events = events
        self.history = history
        self.alerts = None
        self.faces = None
        self.cloud = None
        self._queue: queue.Queue = queue.Queue()
        self._thread: threading.Thread | None = None
        self._last_alert: dict[str, float] = {}
        self._last_photo: tuple[float, str] = (0.0, "")
        self._camera_alerted = False
        self._next_cleanup = 0.0
        events.subscribe(self._on_event)

    # ------------------------------------------------------------------ set-up
    def start(self, alerts=None, faces=None, cloud=None) -> None:
        self.alerts, self.faces, self.cloud = alerts, faces, cloud
        if self._thread is None:
            self._thread = threading.Thread(target=self._run, name="recorder", daemon=True)
            self._thread.start()

    def close(self) -> None:
        self.events.unsubscribe(self._on_event)
        self._queue.put(None)
        if self._thread is not None:
            self._thread.join(timeout=3.0)

    def idle(self, timeout: float = 5.0) -> bool:
        """Wait until every event logged so far has been handled (for tests)."""
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            if self._queue.unfinished_tasks == 0:
                return True
            time.sleep(0.02)
        return False

    # ----------------------------------------------------------------- intake
    def _on_event(self, _stamp: str, level: str, event: str, message: str) -> None:
        """Runs on whichever thread logged the event: only queue it."""
        self._queue.put((time.time(), level, event, message))

    def _run(self) -> None:
        while True:
            try:
                item = self._queue.get(timeout=60.0)
            except queue.Empty:
                item = False
            if item is None:
                self._queue.task_done()
                return
            try:
                if item:
                    self._handle(*item)
                if time.monotonic() >= self._next_cleanup:
                    self._next_cleanup = time.monotonic() + CLEANUP_EVERY
                    self.history.cleanup()
            except Exception as exc:  # one bad event must not end the record
                try:
                    self.events.error("history.error", f"{type(exc).__name__}: {exc}")
                except Exception:
                    pass
            finally:
                if item:
                    self._queue.task_done()

    # --------------------------------------------------------------- one event
    def _handle(self, ts: float, level: str, event: str, message: str) -> None:
        record = classify(event, message, level,
                          self.config.getfloat("doorbell", "answer_seconds", 120.0))
        if record is None:
            return
        photo = self._picture(record.photo, ts) if record.photo else ""
        self.history.add(record.category, record.method, record.person, event, message,
                         level, photo, ts)
        if self.cloud is not None:
            self.cloud.kick()
        if record.alert:
            self._alert(record, photo, ts)

    def _picture(self, tag: str, ts: float) -> str:
        """File name of a picture for this event, or "" (no camera)."""
        if self.faces is None or not self.config.getbool("photos", "enabled", True):
            return ""
        taken, name = self._last_photo
        if name and time.monotonic() - taken < PHOTO_REUSE:
            return name  # same moment, same scene: one picture is enough
        # Minutes late (the queue was backed up)?  The scene is long gone.
        if time.time() - ts > 10.0:
            return ""
        jpeg = self.faces.snapshot_jpeg(timeout=1.5)
        if not jpeg:
            return ""
        name = self.history.save_photo(jpeg, tag, ts)
        if name:
            self._last_photo = (time.monotonic(), name)
        return name

    def _alert(self, record: Record, photo: str, ts: float) -> None:
        if self.alerts is None:
            return
        cfg = self.config
        if record.setting and not cfg.getbool("notify", record.setting, False):
            return
        if record.needs_photo and not photo:
            return
        key = f"{record.alert}.{record.alert_event}"
        if record.alert == "camera":
            # "Back online" only makes sense after an "offline" that was sent.
            if record.alert_event == "online":
                if not self._camera_alerted:
                    return
                self._camera_alerted = False
            else:
                self._camera_alerted = True
        now = time.monotonic()
        if not record.urgent:
            gap = record.gap if record.gap is not None else max(
                0.0, cfg.getfloat("notify", "min_gap_seconds", 20.0))
            last = self._last_alert.get(key)
            if last is not None and now - last < gap:
                return
        self._last_alert[key] = now
        lines = [record.text]
        if record.photo and not photo and record.category in (H.DENIED, H.DOORBELL):
            lines.append("📷 بدون صورة (الكاميرا غير متاحة).")
        lines.append(clock_line(ts))
        options = None
        if record.ask:
            options = [{"id": "yes", "title": "نعم، افتح"}, {"id": "no", "title": "لا"}]
        self.alerts.add(record.alert, record.alert_event, "\n".join(lines),
                        photo=photo, ask=record.ask, options=options)


__all__ = ["Recorder", "Record", "classify"]
