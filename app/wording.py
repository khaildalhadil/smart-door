"""Shared wording for WhatsApp texts: durations, clock times, method names.

Every WhatsApp text -- alert, command reply -- is written in Arabic and in
English at the same time, as one :class:`Texts`.  whatsapp/bot.js sends the
languages listed in ``[bot] languages`` ("ar en": the Arabic message, then the
same in English), each as its own WhatsApp message.
"""

from __future__ import annotations

import time
from dataclasses import dataclass


@dataclass(frozen=True)
class Texts:
    """One WhatsApp text in Arabic (``ar``) and in English (``en``).

    Both are written in the same pass, so an action with side effects (open
    the door, create a guest code, take a picture) runs once, however many
    languages the bot then sends.
    """

    ar: str
    en: str

    def pick(self, ar: bool) -> str:
        """The Arabic or the English text (the other one if that one is empty)."""
        return (self.ar or self.en) if ar else (self.en or self.ar)

    def as_dict(self) -> dict[str, str]:
        """``{"ar": ..., "en": ...}`` as the API sends it ("texts"); a language
        without text is left out."""
        return {lang: text for lang, text in (("ar", self.ar), ("en", self.en)) if text}

    @staticmethod
    def join(parts, sep: str = "\n") -> Texts:
        """Pieces (usually lines) joined language by language; a plain string,
        such as a time, is the same in both."""
        both = [p if isinstance(p, Texts) else Texts(p, p) for p in parts]
        return Texts(sep.join(p.ar for p in both), sep.join(p.en for p in both))


def has_arabic(text: str) -> bool:
    """True when ``text`` has at least one Arabic letter."""
    return any("؀" <= ch <= "ۿ" for ch in text)


# How the door was opened / what was refused, as shown to the owner.
METHOD_AR = {
    "keypad": "رمز الباب",
    "temp_code": "رمز مؤقت",
    "face": "الوجه",
    "fingerprint": "البصمة",
    "card": "الكرت",
    "whatsapp": "واتساب",
    "doorbell": "الجرس",
    "menu": "لوحة التحكم",
    "login": "PIN",
    "fire": "حريق",
    "smoke": "دخان",
    "test": "تجربة",
}
METHOD_EN = {
    "keypad": "door code",
    "temp_code": "guest code",
    "face": "face",
    "fingerprint": "fingerprint",
    "card": "card",
    "whatsapp": "WhatsApp",
    "doorbell": "doorbell",
    "menu": "panel menu",
    "login": "PIN",
    "fire": "fire",
    "smoke": "smoke",
    "test": "test",
}


def _ar_count(n: int, one: str, two: str, few: str, many: str) -> str:
    """Arabic number agreement: 1, 2, 3-10, 11+."""
    if n == 1:
        return one
    if n == 2:
        return two
    if 3 <= n <= 10:
        return f"{n} {few}"
    return f"{n} {many}"


def ar_duration(seconds: float) -> str:
    """90 -> "90 ثانية", 120 -> "دقيقتين", 3600 -> "ساعة"."""
    s = int(round(seconds))
    if s >= 3600 and s % 3600 == 0:
        return _ar_count(s // 3600, "ساعة", "ساعتين", "ساعات", "ساعة")
    if s >= 60 and s % 60 == 0:
        return _ar_count(s // 60, "دقيقة", "دقيقتين", "دقائق", "دقيقة")
    return _ar_count(s, "ثانية", "ثانيتين", "ثوانٍ", "ثانية")


def en_duration(seconds: float) -> str:
    """90 -> "90 seconds", 120 -> "2 minutes", 3600 -> "1 hour"."""
    s = int(round(seconds))
    if s >= 3600 and s % 3600 == 0:
        n, unit = s // 3600, "hour"
    elif s >= 60 and s % 60 == 0:
        n, unit = s // 60, "minute"
    else:
        n, unit = s, "second"
    return f"{n} {unit}{'' if n == 1 else 's'}"


def clock(ts: float | None = None) -> str:
    """HH:MM, local time."""
    return time.strftime("%H:%M", time.localtime(ts) if ts is not None else time.localtime())


def clock_line(ts: float | None = None, ar: bool = True) -> str:
    """The last line of every alert: "الساعة 14:05", in English "Time: 14:05"."""
    return ("الساعة " if ar else "Time: ") + clock(ts)


def mask_number(sender: str) -> str:
    """A phone number for the log: only its last four digits.  A dashboard
    sender ("web:<name>", app/webchat.py) stays as it is."""
    if sender.startswith("web:"):
        return sender[:20]
    digits = "".join(ch for ch in sender if ch.isdigit())
    return "…" + digits[-4:] if len(digits) >= 4 else (sender[:12] or "?")


def describe(row: dict, arabic: bool = True) -> str:
    """One history row (app/history.py) as a short line for the "log" command."""
    category, method, person = row["category"], row["method"], row["person"]
    event, detail = row["event"], row["detail"]
    who = f" ({person})" if person else ""
    if arabic:
        how = METHOD_AR.get(method, method)
        if category == "entry":
            return f"🚪 دخول — {how}{who}"
        if category == "denied":
            if method == "whatsapp":
                return "🚫 أمر من رقم غير مصرّح"
            return f"⛔ رفض — {how}{who}"
        if category == "doorbell":
            if event == "doorbell.ring":
                return "🔔 جرس الباب"
            if event == "doorbell.timeout":
                return "🔕 الجرس: ما أحد رد"
            return "🔔 الجرس: " + ("المالك فتح" if "opened" in detail
                                   else "المالك رفض" if "declined" in detail else detail)
        if category == "alarm":
            what = {"fire": "الحريق", "smoke": "الدخان"}.get(method, "")
            if "alarm over" in detail:
                return f"✅ انتهى إنذار {what}".rstrip()
            if event == "lock.unlock":
                return f"🚪 انفتح الباب بسبب إنذار {what}".rstrip()
            return f"🔥 إنذار {what}".rstrip()
        if category == "remote":
            return "💬 رمز مؤقت جديد" if event == "tempcode.add" else f"💬 أمر واتساب: {detail}"
        return f"⚙️ {event} {detail}"[:80]
    how = METHOD_EN.get(method, method)
    if category == "entry":
        return f"🚪 Entry — {how}{who}"
    if category == "denied":
        if method == "whatsapp":
            return "🚫 Command from an unauthorised number"
        return f"⛔ Refused — {how}{who}"
    if category == "doorbell":
        if event == "doorbell.ring":
            return "🔔 Doorbell"
        if event == "doorbell.timeout":
            return "🔕 Doorbell: no answer"
        return "🔔 Doorbell: " + ("owner opened" if "opened" in detail
                                  else "owner declined" if "declined" in detail else detail)
    if category == "alarm":
        what = method or "alarm"
        if "alarm over" in detail:
            return f"✅ {what.capitalize()} alarm over"
        if event == "lock.unlock":
            return f"🚪 Door opened by the {what} alarm"
        return f"🔥 {what.capitalize()} alarm"
    if category == "remote":
        return "💬 New guest code" if event == "tempcode.add" else f"💬 WhatsApp command: {detail}"
    return f"⚙️ {event} {detail}"[:80]
