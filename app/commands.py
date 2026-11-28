"""WhatsApp commands understood by keyword: no AI, so no tokens.

The bot forwards every incoming message here first:

    POST /command {"text": "افتح الباب", "from": "9689xxxxxxx"}
      -> {"ok": true, "handled": true, "intent": "open",
          "reply": "🚪 تم فتح الباب ...", "photo": null,
          "texts": {"ar": "🚪 تم فتح الباب ...", "en": "🚪 Door opened ..."}}

Every reply is written in Arabic and in English in one go (``texts``, from a
:class:`app.wording.Texts`), so the action behind it runs once whatever the
bot sends: whatsapp/bot.js sends the languages of ``[bot] languages``.
``reply`` is the one in the language of the message (Arabic, unless it was
written in English), for bots that send a single text.

``handled`` true: the bot sends the reply (and ``photo``, if there is one) and
is done.  ``handled`` false: the text is not a door command.  The bot may then
pass it to its language model, if it has one with credit left; if not, it
sends the reply, which says how to get the list of commands.

Matching is strict on purpose.  A message is a command only when *every* word
in it is known: the verb, the door, and filler such as "please".  "Open the
door" opens it.  "Open the door at five", "who opened the door" and "should I
open the door?" do not, and "don't open the door" certainly does not.

Arabic is matched after light normalisation (hamza and ta-marbuta forms,
diacritics, Arabic digits), Gulf dialect verbs included.  A one-letter typo in
the verb is forgiven: locking always, opening only when the door is named.
"""

from __future__ import annotations

import re
import secrets
import time
import unicodedata

from . import doorbell as bell
from . import history as H
from .wording import (METHOD_AR, METHOD_EN, Texts, ar_duration, clock, describe,
                      en_duration, has_arabic, mask_number)

# ---------------------------------------------------------------- normalising
_MARKS = re.compile("[ؐ-ًؚ-ٰٟۖ-ۭـ"
                    "​-‏‪-‮⁦-⁩]")
_FOLD = str.maketrans({"أ": "ا", "إ": "ا", "آ": "ا", "ٱ": "ا", "ة": "ه", "ى": "ي",
                       "ؤ": "و", "ئ": "ي"})
_DIGIT_EDGE = re.compile(r"(?<=\d)(?=[^\d\s])|(?<=[^\d\s])(?=\d)")


def normalize(text: str) -> str:
    """Lower-case, Arabic letter variants folded, digits made ASCII,
    punctuation dropped; words separated by single spaces."""
    text = unicodedata.normalize("NFKC", text).casefold()
    text = _MARKS.sub("", text).translate(_FOLD)
    out = []
    for ch in text:
        if ch.isdecimal():
            out.append(str(unicodedata.decimal(ch)))
        elif ch.isalnum():
            out.append(ch)
        elif ch not in "'’`":       # don't -> dont
            out.append(" ")
    return " ".join(_DIGIT_EDGE.sub(" ", "".join(out)).split())


def _w(words: str) -> frozenset[str]:
    return frozenset(normalize(w) for w in words.split())


# ------------------------------------------------------------------ vocabulary
OPEN = _w("افتح افتحي افتحه افتحها افتحو افتحوا افتحلي افتحله open unlock")
# Dialect or ambiguous on their own: an open command only with the door named.
OPEN_NEEDS_DOOR = _w("فتح بطل بطلي بطله فك فكي فكه شرع شرعي")
LOCK = _w("اقفل اقفلي اقفله اقفلها قفل قفله قفلي سكر سكره سكري سكرها اغلق اغلقه اغلقي غلق "
          "صك صكه صكي بند بنده سد سده lock close shut")
DOOR = _w("الباب باب بابي بابنا بابه البوابة بوابة door doors gate")
LOCK_NOUN = _w("lock القفل قفل")           # "open the lock"
FILLER = _w("لو سمحت سمحتي فضلك فضلكم رجاء الرجاء ممكن تكفى please pls plz the a an my our "
            "now الان الحين حالا بسرعة سريع فورا يا can could would will you u kindly "
            "له لها لهم لهن لي لنا for me him her them us it this that to "
            "هذا هذي ذا عاد بس و and just quickly quick fast immediately "
            "الرئيسي الامامي البيت بيت home house front main bot بوت يلا يالله")
GREET = _w("مرحبا هلا اهلا السلام سلام عليكم وعليكم هاي hi hello hey صباح مساء الخير النور salam")
EXTRA = FILLER | GREET
# Any of these makes the message a question or a refusal, never an order.
QUESTION = _w("هل is كيف شو وش ايش ليش ليه لماذا متى وين كم what whats how hows why when "
              "where should shall did does do")
WHO = _w("من مين منو who whos")
NEG = _w("لا ما مو مب مش لن dont not never no لاتفتح ماتفتح بدون")
NEG_EXTRA = _w("do")                       # "do not open"
OPENISH = OPEN | OPEN_NEEDS_DOOR | _w("تفتح تفتحه تفتحها تفتحي تفتحون تفتحوا opening opened")

# Answers to the doorbell question; they only mean something while it waits.
YES = _w("نعم اي ايه ايوه ايوا اه يب اكيد yes y yep yeah yup ok okay okey k اوكي اوك تمام "
         "طيب زين sure ماشي موافق خله خليه خلها خليها يدخل تدخل يدخلون دخله دخلها دخلهم "
         "let in enter come")
NO = _w("لا no n nope nah رفض ارفض deny reject ابدا مستحيل never")

CODE = _w("رمز الرمز كود الكود code pin passcode otp")
CODE_EXTRA = _w("مؤقت المؤقت مؤقتة temp temporary guest ضيف للضيف الضيف زائر للزائر visitor "
                "جديد new سوي سو سولي اعطني عطني اعطيني ابي ابغى ابغي اريد give make create "
                "generate set add اضف أنشئ ولد one time مرة وحدة واحدة لمدة مدة مدته valid "
                "صالح يصلح صلاحية صلاحيته with of دخول")
UNITS: dict[str, int] = {}
for _words, _seconds in (("ثانية ثواني ثوان ث s sec secs second seconds", 1),
                         ("دقيقة دقايق دقائق د m min mins minute minutes", 60),
                         ("ساعة ساعات س h hr hrs hour hours", 3600),
                         ("يوم ايام d day days", 86400)):
    for _word in _w(_words):
        UNITS[_word] = _seconds
DUALS = {normalize(k): v for k, v in
         {"ثانيتين": 2, "دقيقتين": 120, "ساعتين": 7200, "يومين": 172800}.items()}
FRACTIONS = {normalize(k): v for k, v in
             {"نص": 0.5, "نصف": 0.5, "ربع": 0.25, "half": 0.5, "quarter": 0.25}.items()}

PHOTO = _w("صورة صور صورلي صوري تصوير كاميرا الكاميرا photo pic picture snapshot snap camera "
           "cam image shot")
SHOW = _w("ورني وريني شوف اشوف show see send ارسل ارسلي خذ take")
AT = _w("عند at there بره برا outside موجود واقف is")
LOG = _w("السجل سجل الاحداث احداث اخر الدخول دخل دخلو دخلوا log logs history events last "
         "recent activity entries entered صار")
LOG_EXTRA = _w("شو ايش وش what happened")
TODAY = _w("اليوم today")
SUMMARY = _w("ملخص الملخص تقرير التقرير إحصائيات الإحصائيات إحصائية summary report stats "
             "statistics")
STATUS = _w("الحالة حالة حالته الوضع وضع وضعه status state")
STATE_ADJ = _w("مفتوح مقفل مقفول مسكر مغلق مصكوك open opened locked closed unlocked shut")
HELP = _w("مساعدة المساعدة ساعدني help الاوامر اوامر commands command menu القائمة قائمة "
          "start تسوي تقدر تعرف")

VOCAB = (OPEN | OPEN_NEEDS_DOOR | LOCK | DOOR | LOCK_NOUN | EXTRA | QUESTION | WHO | NEG
         | OPENISH | YES | NO | CODE | CODE_EXTRA | frozenset(UNITS) | frozenset(DUALS)
         | frozenset(FRACTIONS) | PHOTO | SHOW | AT | LOG | LOG_EXTRA | TODAY | SUMMARY
         | STATUS | STATE_ADJ | HELP)
# Verbs a typo may still reach.
_TYPO = {**{w: "open" for w in OPEN if len(w) >= 4},
         **{w: "lock" for w in LOCK if len(w) >= 4}}

MIN_SECONDS, MAX_SECONDS = 10, 24 * 3600   # same limits as app/temp_codes.py


def tokens(text: str) -> list[str]:
    """Normalised words; "من فضلك" loses its "من" (it is not the question
    "who"), and a leading و ("and") comes off a known word."""
    words = normalize(text).replace("من فضلك", "فضلك").split()
    return [w[1:] if (w not in VOCAB and len(w) > 2 and w[0] == "و" and w[1:] in VOCAB)
            else w for w in words]


def _one_edit(a: str, b: str) -> bool:
    """One letter added, dropped, changed, or two neighbours swapped."""
    la, lb = len(a), len(b)
    if a == b or abs(la - lb) > 1:
        return False
    if la == lb:
        diff = [i for i in range(la) if a[i] != b[i]]
        if len(diff) == 1:
            return True
        return (len(diff) == 2 and diff[1] == diff[0] + 1
                and a[diff[0]] == b[diff[1]] and a[diff[1]] == b[diff[0]])
    if la > lb:
        a, b = b, a
    return any(b[:i] + b[i + 1:] == a for i in range(len(b)))


def _typo(word: str) -> str | None:
    """"open" / "lock" when ``word`` is one slip away from such a verb."""
    if len(word) < 4 or word in VOCAB:
        return None
    for known, kind in _TYPO.items():
        if _one_edit(word, known):
            return kind
    return None


def _action(toks: list[str]) -> str | None:
    """"open" or "lock" when the message is that order and nothing else."""
    words = set(toks)
    if words & (QUESTION | WHO | NEG | NO):
        return None
    for i, tok in enumerate(toks):
        typo = False
        if tok in OPEN or tok in OPEN_NEEDS_DOOR:
            kind = "open"
        elif tok in LOCK:
            kind = "lock"
        else:
            kind = _typo(tok)
            typo = kind is not None
        if kind is None:
            continue
        rest = toks[:i] + toks[i + 1:]
        # "ok, open the door": a yes-word next to the order changes nothing.
        if not all(r in DOOR or r in LOCK_NOUN or r in EXTRA or r in YES for r in rest):
            return None
        named = any(r in DOOR or r in LOCK_NOUN for r in rest)
        if kind == "open" and (typo or tok in OPEN_NEEDS_DOOR) and not named:
            return None
        return kind
    return None


def parse_duration(toks: list[str]) -> tuple[float | None, set[int]]:
    """Seconds named in the message ("10 دقايق", "ساعة ونص", "2h") and the
    positions of the words that said so."""
    total, used, last_unit = 0.0, set(), 0
    i = 0
    while i < len(toks):
        tok = toks[i]
        nxt = toks[i + 1] if i + 1 < len(toks) else ""
        if tok.isdigit() and nxt in UNITS:
            last_unit = UNITS[nxt]
            total += int(tok) * last_unit
            used |= {i, i + 1}
            i += 2
        elif tok in FRACTIONS and nxt in UNITS:
            last_unit = UNITS[nxt]
            total += FRACTIONS[tok] * last_unit
            used |= {i, i + 1}
            i += 2
        elif tok in DUALS:
            total += DUALS[tok]
            last_unit = DUALS[tok] // 2
            used.add(i)
            i += 1
        elif tok in UNITS and len(tok) > 1:     # "ساعة" alone is one hour
            last_unit = UNITS[tok]
            total += last_unit
            used.add(i)
            i += 1
        elif tok in FRACTIONS and last_unit:    # "ساعة ونص"
            total += FRACTIONS[tok] * last_unit
            used.add(i)
            i += 1
        else:
            i += 1
    return (total if used else None), used


def _digits(text: str) -> str:
    return "".join(str(unicodedata.decimal(ch)) for ch in text if ch.isdecimal())


# ---------------------------------------------------------------------- router
class CommandRouter:
    """``ctx`` is the app's :class:`app.ui.app.Context`."""

    def __init__(self, ctx):
        self.ctx = ctx
        from .ai import Assistant      # OpenAI, for what the keywords don't cover
        self.ai = Assistant(ctx, self)

    # ------------------------------------------------------------- entry point
    def handle(self, text: str, sender: str = "", self_chat: bool = False,
               verified: bool = False) -> dict:
        """The answer to one WhatsApp message, as the API returns it.
        ``self_chat``: it came from the owner's "message yourself" chat, which
        also holds their own notes.  ``verified``: the caller has already
        checked who wrote it (app/webchat.py: a dashboard admin), so the
        ``[bot] owners`` phone list does not apply."""
        raw = (text or "").strip()
        ar = has_arabic(raw) or not any(ch.isalpha() for ch in raw)
        events = self.ctx.events
        if not verified and not self._authorized(sender):
            shown = "".join(ch if ch.isprintable() else " " for ch in raw[:60])
            events.warning("bot.deny", f"{mask_number(sender)}: {shown}")
            return self._result("denied", Texts(
                "🚫 هذا الرقم غير مصرّح له بالتحكم بالباب.",
                "🚫 This number is not allowed to control the door."), ar=ar)
        toks = tokens(raw)
        doorbell = getattr(self.ctx, "doorbell", None)
        pending = doorbell is not None and doorbell.pending() > 0
        intent = self._intent(toks, "?" in raw or "؟" in raw, pending)
        result = getattr(self, "_do_" + intent)(toks, ar, sender)
        if not result["handled"] and self.ai.ready:
            result = self.ai.answer(raw, sender, ar, self_chat, fallback=result)
        elif result["handled"]:
            self.ai.remember(sender, raw, result["reply"], result["intent"])
        if result["handled"]:
            # The words themselves stay out of the log: they may hold a code.
            events.info("bot.command", f"{result['intent']} from {mask_number(sender)}")
        return result

    def _authorized(self, sender: str) -> bool:
        owners = [_digits(o) for o in
                  self.ctx.config.get("bot", "owners").replace(";", ",").split(",")]
        owners = [o for o in owners if len(o) >= 6]
        if not owners:
            return True   # the bot decides who may talk to it
        number = _digits(sender or "")
        return len(number) >= 6 and any(
            number == o or (len(number) >= 8 and len(o) >= 8
                            and (number.endswith(o) or o.endswith(number)))
            for o in owners)

    # ------------------------------------------------------------------ intent
    def _intent(self, toks: list[str], question: bool, pending: bool) -> str:
        words = set(toks)
        if not toks:
            return "help" if question else "unknown"
        refusal = NO | NEG | NEG_EXTRA | OPENISH | DOOR | LOCK_NOUN | EXTRA
        if pending:   # the doorbell question comes before anything else
            if words & (NO | NEG) and words <= refusal:
                return "bell_no"
            if words & YES and words <= YES | OPEN | OPEN_NEEDS_DOOR | DOOR | EXTRA:
                return "bell_yes"
        if words & NEG and words & OPENISH and words <= refusal:
            return "keep_shut"
        action = None if question else _action(toks)
        if action:
            return "bell_yes" if action == "open" and pending else action
        if words & CODE:
            return "tempcode"
        if ((words & PHOTO and words <= PHOTO | SHOW | WHO | AT | DOOR | EXTRA)
                or (words & WHO and words & (AT | DOOR)
                    and words <= WHO | AT | DOOR | SHOW | EXTRA)):
            return "photo"
        numbers = {t for t in toks if t.isdigit()}
        if words & LOG and words <= LOG | LOG_EXTRA | TODAY | WHO | DOOR | EXTRA | numbers:
            return "log"
        if words & (SUMMARY | TODAY) and words <= SUMMARY | TODAY | DOOR | EXTRA:
            return "summary"
        about_door = STATUS | STATE_ADJ | QUESTION | DOOR | LOCK_NOUN | OPENISH | LOCK | EXTRA
        if words <= about_door and (
                words & STATUS
                or (words & (DOOR | LOCK_NOUN) and words & STATE_ADJ)
                or (question and words & (DOOR | LOCK_NOUN | OPENISH | LOCK))):
            return "status"
        if words & (HELP | GREET) and words <= HELP | GREET | QUESTION | EXTRA:
            return "help"
        if words & (YES | NO) and words <= YES | NO | EXTRA:
            return "no_bell"
        return "unknown"

    # ---------------------------------------------------------------- helpers
    @staticmethod
    def _t(ar: bool, arabic: str, english: str) -> str:
        """One language only.  Replies are :class:`Texts`, both languages."""
        return arabic if ar else english

    @staticmethod
    def _result(intent: str, reply: Texts | str, handled: bool = True, photo: str = "",
                ar: bool = True) -> dict:
        """The answer as the API returns it.  ``reply`` in both languages goes
        out as ``texts``, and ``ar`` picks the one for ``reply``; a plain
        string is the reply as it is (no ``texts``)."""
        texts = reply if isinstance(reply, Texts) else None
        result = {"handled": handled, "intent": intent,
                  "reply": texts.pick(ar) if texts is not None else reply,
                  "photo": f"/photos/{photo}" if photo else None}
        if texts is not None:
            result["texts"] = texts.as_dict()
        return result

    def _alarms(self) -> list[str]:
        hazards = getattr(self.ctx, "hazards", None)
        return hazards.alarms() if hazards is not None else []

    # ----------------------------------------------------------------- actions
    @staticmethod
    def _channel(sender: str) -> str:
        """The lock's reason for a remote command: "web" for the dashboard
        chat (app/webchat.py), else "whatsapp"."""
        return "web" if sender.startswith("web:") else "whatsapp"

    def _do_open(self, _toks, ar: bool, sender: str) -> dict:
        lock, cfg = self.ctx.lock, self.ctx.config
        if lock.backend.simulated:
            return self._result("open", Texts(
                "⚠️ القفل في وضع المحاكاة (الريلاي غير متصل): الباب ما ينفتح فعليًا.",
                "⚠️ The lock is in simulation mode (no relay): the door cannot open."), ar=ar)
        if not lock.unlock(reason=self._channel(sender)):
            return self._result("open", Texts(
                "⚠️ عطل في الريلاي: الباب ما انفتح. راجع سجل الأحداث على اللوحة.",
                "⚠️ Relay fault: the door did not open. See the event log on the panel."), ar=ar)
        seconds = cfg.unlock_seconds
        return self._result("open", Texts(
            f"🚪 تم فتح الباب، ويقفل تلقائيًا بعد {ar_duration(seconds)}.",
            f"🚪 Door opened. It locks again in {en_duration(seconds)}."), ar=ar)

    def _do_lock(self, _toks, ar: bool, sender: str) -> dict:
        lock = self.ctx.lock
        was_open = lock.status().is_unlocked
        lock.lock(reason=self._channel(sender))
        lines = [Texts("🔒 تم قفل الباب.", "🔒 Door locked.") if was_open else
                 Texts("🔒 الباب مقفل أصلًا.", "🔒 The door is already locked.")]
        if self._alarms():
            lines.append(Texts(
                "⚠️ إنذار شغّال الآن: النظام يعيد فتح الباب تلقائيًا للسلامة.",
                "⚠️ An alarm is active: the system re-opens the door for safety."))
        return self._result("lock", Texts.join(lines), ar=ar)

    def _do_keep_shut(self, _toks, ar: bool, _sender: str) -> dict:
        if self.ctx.lock.status().is_unlocked:
            return self._result("keep_shut", Texts(
                "الباب مفتوح الآن ويقفل تلقائيًا بعد ثوانٍ. اكتب «اقفل الباب» لقفله فورًا.",
                "The door is open right now and relocks in a few seconds. "
                "Send \"lock door\" to lock it at once."), ar=ar)
        return self._result("keep_shut", Texts(
            "👌 تمام، الباب مقفل وما راح أفتحه.", "👌 OK, the door stays locked."), ar=ar)

    # ---------------------------------------------------------------- doorbell
    def _do_bell_yes(self, toks, ar: bool, sender: str) -> dict:
        outcome = self.ctx.doorbell.answer(True, mask_number(sender) if sender else "")
        if outcome == bell.OPENED:
            return self._result("bell_open", Texts(
                "🚪 تم فتح الباب للزائر.", "🚪 Door opened for the visitor."), ar=ar)
        if outcome == bell.FAULT:
            return self._result("bell_open", Texts(
                "⚠️ عطل في الريلاي: الباب ما انفتح للزائر.",
                "⚠️ Relay fault: the door did not open for the visitor."), ar=ar)
        # The ring ran out between the check and the answer.
        return (self._do_open(toks, ar, sender) if _action(toks) == "open"
                else self._do_no_bell(toks, ar, sender))

    def _do_bell_no(self, toks, ar: bool, sender: str) -> dict:
        outcome = self.ctx.doorbell.answer(False, mask_number(sender) if sender else "")
        if outcome == bell.DECLINED:
            return self._result("bell_deny", Texts(
                "👌 تمام، ما فتحت الباب. الزائر تبلّغ على الشاشة.",
                "👌 OK, the door stays locked. The visitor was told on the screen."), ar=ar)
        return self._do_no_bell(toks, ar, sender)

    def _do_no_bell(self, _toks, ar: bool, _sender: str) -> dict:
        return self._result("no_bell", Texts(
            "ما في جرس ينتظر الرد الآن.\nإذا تبي تفتح اكتب «افتح الباب».",
            "No doorbell is waiting for an answer.\nSend \"open door\" to open it."),
            handled=False, ar=ar)

    # --------------------------------------------------------------- temp code
    def _do_tempcode(self, toks, ar: bool, _sender: str) -> dict:
        cfg = self.ctx.config
        # "رمز 7788 ساعة" is code 7788 for an hour, not 7788 hours: the only
        # number in the message, if it looks like a code, is the code.  (A
        # plain count of seconds, "3600 seconds", stays a duration.)
        at = [i for i, t in enumerate(toks) if t.isdigit()]
        code = None
        if len(at) == 1 and 4 <= len(toks[at[0]]) <= 8:
            nxt = toks[at[0] + 1] if at[0] + 1 < len(toks) else ""
            if not (UNITS.get(nxt) == 1 and int(toks[at[0]]) <= MAX_SECONDS):
                code = toks[at[0]]
                toks = toks[:at[0]] + toks[at[0] + 1:]
        seconds, used = parse_duration(toks)
        rest = [t for i, t in enumerate(toks) if i not in used]
        numbers = [t for t in rest if t.isdigit()]
        words = [t for t in rest if not t.isdigit()]
        if not all(w in CODE or w in CODE_EXTRA or w in EXTRA or w in DOOR for w in words):
            return self._do_unknown(toks, ar, _sender)
        if code is None:
            code = next((n for n in numbers if 4 <= len(n) <= 8), None)
        others = [n for n in numbers if n is not code]
        if seconds is None and len(others) == 1 and len(others[0]) <= 4:
            seconds = int(others.pop()) * 60       # "رمز 4455 30": minutes
        if others:
            return self._result("tempcode", Texts(
                "⚠️ الرمز المؤقت لازم يكون من 4 إلى 8 أرقام.\n"
                "مثال: رمز 4455 لمدة 30 دقيقة",
                "⚠️ A guest code must be 4 to 8 digits.\nExample: code 4455 for 30 minutes"),
                ar=ar)
        return self._issue_tempcode(code, seconds, ar)

    def _issue_tempcode(self, code: str | None, seconds: float | None, ar: bool) -> dict:
        """A one-time guest code: ``code`` None = random, ``seconds`` None = default."""
        cfg = self.ctx.config
        if seconds is None:
            seconds = cfg.getfloat("bot", "temp_code_minutes", 1.0) * 60
        seconds = int(max(MIN_SECONDS, min(seconds, MAX_SECONDS)))
        if code is None:
            length = max(4, min(cfg.getint("bot", "temp_code_digits", 6), 8))
            code = "0" * length
            while len(set(code)) == 1:
                code = "".join(secrets.choice("0123456789") for _ in range(length))
        try:
            self.ctx.temp_codes.add(code, seconds)
        except ValueError as exc:
            limit = cfg.getint("api", "max_temp_codes", 20)
            if "too many" in str(exc):
                return self._result("tempcode", Texts(
                    f"⚠️ فيه {limit} رمزًا مؤقتًا فعّالًا، وهذا الحد الأقصى. "
                    "انتظر لين ينتهي واحد منها.",
                    f"⚠️ {limit} guest codes are already active, which is the limit. "
                    "Wait for one to expire."), ar=ar)
            return self._result("tempcode", Texts(
                "⚠️ ما قدرت أسوي الرمز المؤقت. الرمز من 4 إلى 8 أرقام.",
                "⚠️ Could not create the guest code. A code is 4 to 8 digits."), ar=ar)
        return self._result("tempcode", Texts(
            f"🔑 رمز مؤقت: {code}\n"
            f"⏱ صالح لمدة {ar_duration(seconds)}، ويفتح مرة واحدة فقط.\n"
            "يُكتب على لوحة الباب ثم #",
            f"🔑 Guest code: {code}\n"
            f"⏱ Valid for {en_duration(seconds)}, and opens the door once.\n"
            "Type it on the door keypad, then #"), ar=ar)

    # ------------------------------------------------------------------- photo
    def _do_photo(self, _toks, ar: bool, _sender: str) -> dict:
        faces, history = self.ctx.faces, self.ctx.history
        jpeg = faces.snapshot_jpeg() if faces is not None else None
        name = history.save_photo(jpeg, "request") if jpeg else ""
        if not name and faces is not None and getattr(faces, "last_blank", False):
            return self._result("photo", Texts(
                "📷 الكاميرا ما أعطت إلا صورة سوداء، فما أرسلتها.",
                "📷 The camera gave only a black picture, so none was sent."), ar=ar)
        if not name:
            return self._result("photo", Texts(
                "📷 الكاميرا غير متاحة الآن.", "📷 The camera is not available right now."),
                ar=ar)
        now = clock()   # one reading for both languages
        return self._result("photo", Texts(
            f"📷 صورة من كاميرا الباب الآن ({now}).",
            f"📷 Door camera, just now ({now})."), photo=name, ar=ar)

    # ----------------------------------------------------------- log / summary
    def _do_log(self, toks, ar: bool, _sender: str) -> dict:
        return self._events(bool(set(toks) & TODAY),
                            next((int(t) for t in toks if t.isdigit()), 8), ar)

    def _events(self, today: bool, wanted: int, ar: bool) -> dict:
        rows = self.ctx.history.recent(
            limit=max(1, min(wanted, 15)),
            categories=(H.ENTRY, H.DENIED, H.DOORBELL, H.ALARM),
            since=H.start_of_day() if today else None)
        if not rows:
            return self._result("log", Texts(
                "🧾 لا توجد أحداث مسجّلة" + (" اليوم." if today else " بعد."),
                "🧾 No events recorded" + (" today." if today else " yet.")), ar=ar)
        midnight = H.start_of_day()
        lines = [Texts("🧾 أحداث اليوم:" if today else "🧾 آخر الأحداث:",
                       "🧾 Today's events:" if today else "🧾 Latest events:")]
        for row in rows:
            stamp = time.localtime(row["ts"])
            when = time.strftime("%H:%M" if row["ts"] >= midnight else "%d/%m %H:%M", stamp)
            lines.append(Texts(f"{when}  {describe(row, True)}", f"{when}  {describe(row, False)}"))
        return self._result("log", Texts.join(lines), ar=ar)

    def _summary_lines(self) -> list[Texts]:
        data = self.ctx.history.summary(H.start_of_day())

        def split(counts: dict, names: dict) -> str:
            if not counts:
                return ""
            parts = [f"{names.get(m, m or '?')} {n}"
                     for m, n in sorted(counts.items(), key=lambda kv: -kv[1])]
            return " (" + " · ".join(parts) + ")"

        entries, denied = data["entries"], data["denied"]
        lines = [
            Texts(f"🚪 الدخول: {sum(entries.values())}{split(entries, METHOD_AR)}",
                  f"🚪 Entries: {sum(entries.values())}{split(entries, METHOD_EN)}"),
            Texts(f"⛔ المحاولات المرفوضة: {sum(denied.values())}{split(denied, METHOD_AR)}",
                  f"⛔ Refused attempts: {sum(denied.values())}{split(denied, METHOD_EN)}"),
            Texts(f"🔔 الجرس: {data['rings']}", f"🔔 Doorbell rings: {data['rings']}"),
        ]
        if data["alarms"]:
            lines.append(Texts(f"🔥 الإنذارات: {data['alarms']}",
                               f"🔥 Alarms: {data['alarms']}"))
        last = data["last_entry"]
        if last:
            day = "%H:%M" if last["ts"] >= H.start_of_day() else "%d/%m %H:%M"
            when = time.strftime(day, time.localtime(last["ts"]))
            how = (METHOD_AR.get(last["method"], last["method"]),
                   METHOD_EN.get(last["method"], last["method"]))
            who = f" ({last['person']})" if last["person"] else ""
            lines.append(Texts(f"🕒 آخر دخول: {when} — {how[0]}{who}",
                               f"🕒 Last entry: {when} — {how[1]}{who}"))
        return lines

    def _do_summary(self, _toks, ar: bool, _sender: str) -> dict:
        head = Texts("📊 ملخص اليوم:", "📊 Today so far:")
        return self._result("summary", Texts.join([head] + self._summary_lines()), ar=ar)

    # ------------------------------------------------------------------ status
    def _do_status(self, _toks, ar: bool, _sender: str) -> dict:
        ctx = self.ctx
        status = ctx.lock.status()
        lines: list[Texts] = []
        if status.error:
            lines.append(Texts("⚠️ عطل في ريلاي القفل", "⚠️ Lock relay fault"))
        elif status.is_unlocked:
            left = max(1, round(status.seconds_remaining))
            lines.append(Texts(f"🔓 الباب مفتوح (يقفل بعد {left} ث)",
                               f"🔓 Door open (locks in {left} s)"))
        else:
            lines.append(Texts("🔒 الباب مقفل", "🔒 Door locked"))
        if ctx.lock.backend.simulated:
            lines.append(Texts("⚠️ القفل في وضع المحاكاة (بدون ريلاي)",
                               "⚠️ Lock in simulation mode (no relay)"))
        for kind in self._alarms():
            what = {"fire": ("حريق", "Fire"), "smoke": ("دخان", "Smoke")}.get(kind, (kind, kind))
            lines.append(Texts(f"🔥 إنذار {what[0]} شغّال الآن!",
                               f"🔥 {what[1]} alarm active now!"))
        doorbell = getattr(ctx, "doorbell", None)
        left = doorbell.pending() if doorbell is not None else 0
        if left > 0:
            lines.append(Texts(f"🔔 جرس ينتظر ردّك (باقي {round(left)} ث): «نعم» أو «لا»",
                               f"🔔 Doorbell waiting for you ({round(left)} s left): yes / no"))
        faces = ctx.faces
        if faces is None or not faces.available:
            camera = ("غير مفعّلة", "not set up")
        else:
            camera = ("شغّالة", "online") if faces.camera_online else ("متوقفة ⚠️", "OFFLINE ⚠️")
        lines.append(Texts(f"📷 الكاميرا: {camera[0]}", f"📷 Camera: {camera[1]}"))
        fp = getattr(ctx, "fingerprints", None)
        if fp is not None and fp.available:
            finger = ("لا ترد ⚠️", "not responding ⚠️") if fp.online is False else ("شغّالة", "ready")
            lines.append(Texts(f"👆 البصمة: {finger[0]}", f"👆 Fingerprint: {finger[1]}"))
        cards = getattr(ctx, "cards", None)
        if cards is not None and cards.available:
            reader = ("لا يرد ⚠️", "not responding ⚠️") if cards.online is False else ("شغّال", "ready")
            lines.append(Texts(f"💳 قارئ الكروت: {reader[0]}", f"💳 Card reader: {reader[1]}"))
        active = ctx.temp_codes.active_count()
        if active:
            lines.append(Texts(f"🔑 رموز مؤقتة فعّالة: {active}",
                               f"🔑 Active guest codes: {active}"))
        cloud = getattr(ctx, "cloud", None)
        if cloud is not None:
            rows, photos = ctx.history.backlog()
            state = cloud.state()
            if state == "off":
                text = ("غير مضبوطة (البيانات محفوظة على الجهاز)", "not set up (data stays on the Pi)")
            elif state == "ok" and not rows and not photos:
                text = ("متصلة ✓", "in sync ✓")
            elif state == "ok":
                text = (f"متصلة، {rows} سجل بانتظار الرفع", f"online, {rows} row(s) to upload")
            else:
                text = (f"غير متصلة ⚠️ ({rows} سجل بانتظار الرفع)",
                        f"offline ⚠️ ({rows} row(s) waiting)")
            lines.append(Texts(f"☁️ قاعدة البيانات: {text[0]}", f"☁️ Database: {text[1]}"))
        lines.append(Texts("— اليوم —", "— Today —"))
        lines += self._summary_lines()
        return self._result("status", Texts.join(lines), ar=ar)

    # -------------------------------------------------------------------- help
    def _do_help(self, _toks, ar: bool, _sender: str) -> dict:
        return self._result("help", Texts(
            "🤖 أوامر الباب:\n"
            "🚪 افتح الباب\n"
            "🔒 اقفل الباب\n"
            "ℹ️ الحالة\n"
            "🔑 رمز مؤقت   (أو: رمز 4455 لمدة 30 دقيقة)\n"
            "📷 صورة   (من عند الباب؟)\n"
            "🧾 السجل   (آخر الأحداث)\n"
            "📊 ملخص اليوم\n"
            "🔔 لما يرن الجرس: رد «نعم» أو «لا»",
            "🤖 Door commands:\n"
            "🚪 open door\n"
            "🔒 lock door\n"
            "ℹ️ status\n"
            "🔑 temp code   (or: code 4455 for 30 minutes)\n"
            "📷 photo   (who is at the door?)\n"
            "🧾 log   (latest events)\n"
            "📊 summary\n"
            "🔔 when the bell rings: answer yes or no"), ar=ar)

    def _do_unknown(self, _toks, ar: bool, _sender: str) -> dict:
        return self._result("unknown", Texts(
            "ما فهمت الطلب 🤔\nاكتب «مساعدة» لعرض أوامر الباب.",
            "I didn't get that 🤔\nSend \"help\" to see the door commands."),
            handled=False, ar=ar)
