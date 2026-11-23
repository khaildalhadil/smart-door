"""Public screens: the door keypad, sign-in and first-run setup."""

from __future__ import annotations

import math
import threading
import tkinter as tk

from .. import doorbell as bell
from ..face import (CAMERA_OFFLINE, FACE_CHECKING, FACE_FAR, FACE_NONE, FACE_SEEN,
                    FACE_UNKNOWN)
from ..users import ROLE_ADMIN
from . import theme
from .app import Screen
from .prompts import PinPrompt, TextPrompt
from .widgets import CameraView, PinPad, TouchButton, TouchList

# Sensor answers that mean "could not read that touch", not "wrong finger".
FP_UNREADABLE = {0x03, 0x06, 0x07, 0x15}
# ... and the ones that mean "this finger is not allowed in".
FP_DENIED = {0x08, 0x09}
# What the door screen says.  The technical reason goes to the event log only.
# Keep each under ~440 px in FONT_BODY_BOLD or the banner cuts it off.
FP_MSG_DENIED = "Access denied — unknown fingerprint"
FP_MSG_UNREADABLE = "Couldn't read the finger — try again"
FP_MSG_OFFLINE = "Fingerprint sensor not responding"
FP_MSG_ERROR = "Fingerprint error — try again"

# Cards (app/nfc.py).  Same width limit.
CARD_MSG_DENIED = "Access denied — unknown card"

# Doorbell: what the visitor reads.  Same width limit as the lines above.
BELL_TEXT = "Ring bell"
BELL_MSG_RINGING = "Ringing… the owner was notified"
BELL_MSG_OFFLINE = "Bell sent — the owner may be offline"
BELL_MSG = {
    bell.OPENED: ("The owner is opening the door", "ok"),
    bell.DECLINED: ("The owner can't open the door now", "warn"),
    bell.TIMEOUT: ("No answer from the owner", "warn"),
    bell.FAULT: ("Relay fault — door not opened.", "error"),
}

# Hint under the camera picture per face state; other states keep the last one.
FACE_HINTS = {
    FACE_NONE: ("Look at the camera", theme.MUTED),
    FACE_FAR: ("Come closer", theme.WARN_HI),
    FACE_CHECKING: ("Checking…", theme.ACCENT_HI),
    FACE_UNKNOWN: ("Face not recognized", theme.DANGER_HI),
    FACE_SEEN: ("Step back to open again", theme.MUTED),
    CAMERA_OFFLINE: ("Camera offline", theme.DANGER_HI),
}


class HomeScreen(Screen):
    """The idle screen: door-code keypad, lock state, doorbell, sign-in, plus
    fingerprint scanning and the live camera when those are in use.  An
    enrolled face in front of the camera, or an enrolled finger on the sensor,
    opens the door by itself."""

    def __init__(self, app, notice: tuple[str, str] | None = None):
        super().__init__(app)
        cfg = self.ctx.config
        self._scanning = False
        self._checking = False
        self._locked_out = False

        left = tk.Frame(self, bg=theme.BG, width=470)
        left.pack(side="left", fill="y", padx=(theme.PAD, 0), pady=theme.PAD)
        left.pack_propagate(False)
        self.prompt = tk.Label(left, text="Enter the code, then press #", font=theme.FONT_BODY,
                               bg=theme.BG, fg=theme.MUTED, anchor="w")
        self.prompt.pack(fill="x", padx=4)
        self.msg = self.banner(left)
        self.msg.pack(fill="x", padx=4, pady=(0, 4))
        self.pad = PinPad(left, cfg.getint("security", "passcode_max_length", 12),
                          self._submit, on_cancel=self._cancel)
        self.pad.pack(fill="both", expand=True)

        right = tk.Frame(self, bg=theme.BG)
        right.pack(side="left", fill="both", expand=True, padx=theme.PAD, pady=theme.PAD)
        self.tile = tk.Frame(right, bg=theme.SURFACE)
        self.tile.pack(fill="both", expand=True, pady=(0, theme.PAD))
        faces = self.ctx.faces
        self.camera = None
        self._face_token = None
        if faces.available:
            # Live picture on top, lock state underneath; the countdown is in
            # the state line, so there is no separate sub-line.
            self.camera = CameraView(self.tile, faces, 296, 222)
            self.camera.pack(pady=(5, 0))
            self.state_text = tk.Label(self.tile, font=theme.FONT_TITLE, bg=theme.SURFACE)
            self.state_text.pack(expand=True, fill="both")
            self.state_sub = None
        else:
            self.state_text = tk.Label(self.tile, font=theme.FONT_STATE, bg=theme.SURFACE)
            self.state_text.pack(expand=True, fill="both", pady=(18, 0))
            self.state_sub = tk.Label(self.tile, font=theme.FONT_BODY, bg=theme.SURFACE,
                                      fg=theme.MUTED, wraplength=280)
            self.state_sub.pack(expand=True, fill="both", pady=(0, 14))

        # Doorbell: a picture and "open?" go to the owner on WhatsApp.
        doorbell = self.ctx.doorbell
        self.bell_button = None
        self._bell_job = None
        if doorbell is not None and doorbell.enabled:
            self.bell_button = TouchButton(right, BELL_TEXT, self._ring, kind="warn")
            self.bell_button.pack(fill="x", ipady=8 if self.camera else 12,
                                  pady=(0, theme.PAD))

        # With the camera taking the room, the two buttons share one row.
        row = tk.Frame(right, bg=theme.BG) if self.camera else right
        if self.camera:
            row.pack(fill="x")
        # Three rows share the column once the bell is there: slimmer buttons.
        row_pad = 8 if self.bell_button else 12
        fp = self.ctx.fingerprints
        self.fp_button = None
        self._fp_token = None
        self._fp_auto = fp.available and fp.auto_scan
        self._fp_text = "Fingerprint" if self.camera else "Scan fingerprint"
        if fp.available and not self._fp_auto:
            self.fp_button = TouchButton(row, self._fp_text, self._fingerprint,
                                         kind="primary")
            if self.camera:
                self.fp_button.pack(side="left", fill="x", expand=True, ipady=row_pad,
                                    padx=(0, theme.PAD // 2))
            else:
                self.fp_button.pack(fill="x", ipady=12, pady=(0, theme.PAD))
        sign_in = TouchButton(row, "Sign in", self._sign_in)
        if self.camera:
            sign_in.pack(side="left", fill="x", expand=True, ipady=row_pad)
        else:
            sign_in.pack(fill="x", ipady=12)
        if self.ctx.lock.backend.simulated and cfg.getbool("ui", "simulation_button", True):
            TouchButton(right, "Simulate unlock (no GPIO)", self._simulate, kind="warn",
                        font=theme.FONT_BODY_BOLD).pack(fill="x", ipady=8,
                                                       pady=(theme.PAD, 0))

        if not self.ctx.passcode.is_configured:
            self.prompt.configure(text="No door passcode set — sign in to set one",
                                  fg=theme.WARN_HI)
        if notice:
            self.msg.show(*notice)
        self._tick_lockout()
        self._bell_tick()   # a ring may still be waiting from before a menu visit
        if self.camera:
            self.camera.hint(*FACE_HINTS[FACE_NONE])
            self._face_token = faces.watch(
                on_state=lambda state: app.post(self._face_state, state),
                on_match=lambda uid, score: app.post(self._face_match, uid, score),
                preview=self.camera.size,
            )
        if self._fp_auto:
            self._fp_token = fp.watch(
                on_finger=lambda: app.post(self._fp_touched),
                on_result=lambda result: app.post(self._fp_result, result),
            )
        cards = self.ctx.cards
        self._card_token = None
        if cards is not None and cards.available:
            self._card_token = cards.watch(lambda uid: app.post(self._card, uid))

    def destroy(self) -> None:
        if self._bell_job is not None:
            self.after_cancel(self._bell_job)
            self._bell_job = None
        if self._scanning:
            self.ctx.fingerprints.cancel()
        if self._fp_token is not None:
            self.ctx.fingerprints.release(self._fp_token)
        if self._face_token is not None:
            self.ctx.faces.release(self._face_token)
        if self._card_token is not None:
            self.ctx.cards.release(self._card_token)
        super().destroy()

    # ---------------------------------------------------------------- lock tile
    def on_lock_status(self, status) -> None:
        if self.camera:
            if status.error:
                bg, text = theme.DANGER, "RELAY FAULT"
            elif status.is_unlocked:
                bg = theme.OK
                text = f"UNLOCKED · {math.ceil(status.seconds_remaining)} s"
            else:
                bg, text = theme.SURFACE, "LOCKED"
            for w in (self.tile, self.state_text):
                w.configure(bg=bg)
            self.camera.set_background(bg, inverted=bg != theme.SURFACE)
            self.state_text.configure(
                text=text, fg="#ffffff" if bg != theme.SURFACE else theme.TEXT)
            return
        if status.error:
            bg, text, sub = theme.DANGER, "RELAY FAULT", status.error[:80]
        elif status.is_unlocked:
            bg, text = theme.OK, "UNLOCKED"
            sub = f"Locks again in {math.ceil(status.seconds_remaining)} s"
        else:
            bg, text = theme.SURFACE, "LOCKED"
            sub = "Code + #" + ("  or touch the sensor" if self._fp_auto
                               else "  or scan a finger" if self.fp_button else " to open")
            if self._card_token is not None:
                sub += ", or a card"
        fg_sub = "#ffffff" if bg != theme.SURFACE else theme.MUTED
        for w in (self.tile, self.state_text, self.state_sub):
            w.configure(bg=bg)
        self.state_text.configure(text=text, fg="#ffffff" if bg != theme.SURFACE else theme.TEXT)
        self.state_sub.configure(text=sub, fg=fg_sub)

    # ------------------------------------------------------------------- alarm
    def on_alarm(self, alarms: list[str]) -> None:
        if not hasattr(self, "_prompt_normal"):
            self._prompt_normal = (self.prompt.cget("text"), self.prompt.cget("fg"))
        if alarms:
            what = " + ".join(kind.upper() for kind in alarms)
            door = " — door opened" if self.ctx.config.getbool("alarm", "open_door", True) else ""
            self.prompt.configure(text=f"{what} ALARM{door}", fg=theme.DANGER_HI,
                                  font=theme.FONT_BODY_BOLD)
        else:
            text, fg = self._prompt_normal
            self.prompt.configure(text=text, fg=fg, font=theme.FONT_BODY)

    # ---------------------------------------------------------------- doorbell
    def _ring(self) -> None:
        doorbell = self.ctx.doorbell
        if not doorbell.ring():
            self.msg.show(f"Already ringing — wait {math.ceil(doorbell.retry_in())} s",
                          "muted")
            return
        alerts = self.ctx.alerts
        if alerts is not None and not alerts.bot_online():
            self.msg.show(BELL_MSG_OFFLINE, "warn", sticky=True)
        else:
            self.msg.show(BELL_MSG_RINGING, "info", sticky=True)
        self._bell_tick()

    def _bell_tick(self) -> None:
        """Count down on the button while the owner has not answered."""
        self._bell_job = None
        if not self.alive or self.bell_button is None:
            return
        doorbell = self.ctx.doorbell
        left = doorbell.pending()
        if left <= 0:
            self.bell_button.configure(text=BELL_TEXT)
            self.bell_button.set_kind("warn")
            return
        self.bell_button.configure(text=f"Ringing…  {math.ceil(left)} s")
        self.bell_button.set_kind("default" if doorbell.retry_in() > 0 else "warn")
        self._bell_job = self.after(500, self._bell_tick)

    def on_doorbell(self, state: str) -> None:
        """The owner answered on WhatsApp, or did not in time."""
        if state in BELL_MSG:
            self.msg.show(*BELL_MSG[state])
        if self._bell_job is None:
            self._bell_tick()

    # ------------------------------------------------------------------ keypad
    def _cancel(self) -> None:
        self.pad.clear()
        if self.ctx.passcode.lockout_remaining() <= 0:
            self.msg.clear()

    def _submit(self, code: str) -> None:
        if not code or self._checking:
            return
        self.pad.clear()
        self._checking = True
        self.pad.set_enabled(False)
        self.msg.show("Checking…", "muted", sticky=True)

        def work():
            # A guest code (POST /tempCode) is tried first so it never counts as
            # a wrong door code; the keypad lockout still applies to it.
            temp_id = None
            if self.ctx.passcode.lockout_remaining() <= 0:
                temp_id = self.ctx.temp_codes.match(code)
            result = None if temp_id else self.ctx.passcode.verify(code)
            self.app.post(self._verified, result, temp_id)

        threading.Thread(target=work, name="verify", daemon=True).start()

    def _verified(self, result, temp_id: str | None = None) -> None:
        self._checking = False
        if not self.alive:
            return
        self.pad.set_enabled(True)
        events = self.ctx.events
        if temp_id:
            # Single use: the code is deleted before the relay is driven.
            if not self.ctx.temp_codes.consume(temp_id):
                self.msg.show("That guest code has expired.", "error")
            elif self.ctx.lock.unlock(reason="temp code"):
                self.msg.show("Access granted", "ok")
            else:
                self.msg.show("Relay fault — door not opened. See the event log.", "error")
        elif result.ok:
            if self.ctx.lock.unlock(reason="keypad"):
                self.msg.show("Access granted", "ok")
            else:
                self.msg.show("Relay fault — door not opened. See the event log.", "error")
        elif result.reason == "not_configured":
            self.msg.show("No door code is set. An administrator must sign in.", "warn")
        elif result.locked_out:
            events.warning("keypad.deny", "wrong passcode, keypad locked")
            self._tick_lockout()
        else:
            events.warning("keypad.deny",
                           f"wrong passcode ({result.remaining_attempts} attempt(s) left)")
            n = result.remaining_attempts
            self.msg.show(f"Wrong code — {n} attempt{'s' if n != 1 else ''} left",
                          "error")

    def _tick_lockout(self) -> None:
        if not self.alive:
            return
        remaining = self.ctx.passcode.lockout_remaining()
        if remaining > 0:
            self._locked_out = True
            self.pad.set_enabled(False)
            self.msg.show(f"Too many attempts. Try again in {math.ceil(remaining)} s",
                          "error", sticky=True)
            self.after(1000, self._tick_lockout)
        elif self._locked_out and not self._checking:
            self._locked_out = False
            self.pad.set_enabled(True)
            self.msg.clear()

    # ------------------------------------------------------------- fingerprint
    def _fingerprint(self) -> None:
        fp = self.ctx.fingerprints
        if self._scanning:
            fp.cancel()
            self.msg.show("Cancelling…", "muted", sticky=True)
            return
        if not fp.available:
            self.msg.show("The fingerprint sensor is not set up yet.", "warn")
            return
        started = fp.identify_async(
            progress=lambda step, total, text: self.app.post(self._fp_progress, text),
            done=lambda result: self.app.post(self._fp_done, result),
        )
        if started:
            self._scanning = self.keep_alive = True
            self.fp_button.configure(text="Cancel scan")
            self.fp_button.set_kind("danger")

    def _fp_progress(self, text: str) -> None:
        if self.alive:
            self.msg.show(text + "…", "info", sticky=True)

    def _fp_done(self, result) -> None:
        """End of a scan started with the button."""
        if not self.alive:
            return
        self._scanning = self.keep_alive = False
        self.fp_button.configure(text=self._fp_text)
        self.fp_button.set_kind("primary")
        self._fp_result(result)

    def _fp_touched(self) -> None:
        """The watch saw a finger land on the sensor."""
        if self.alive:
            self.msg.show("Checking fingerprint…", "info", sticky=True)

    def _fp_result(self, result) -> None:
        if not self.alive:
            return
        events = self.ctx.events
        if not result.ok:
            data = result.data
            code = data.get("code")
            if data.get("cancelled"):
                self.msg.show("Scan cancelled", "muted")
                return
            if data.get("timeout") and code == 0x02:  # nobody put a finger down
                self.msg.show("No finger detected", "warn")
                return
            if code in FP_UNREADABLE:
                self.msg.show(FP_MSG_UNREADABLE, "warn")
                return
            # "deny" is a person refused (it is recorded with a picture and
            # reported to the owner); a sensor that fails is a "fault".
            if code in FP_DENIED:
                events.warning("fingerprint.deny", result.message)
                self.msg.show(FP_MSG_DENIED, "error")
            elif data.get("timeout"):
                events.warning("fingerprint.fault", result.message)
                self.msg.show(FP_MSG_OFFLINE, "error")
            else:
                events.warning("fingerprint.fault", result.message)
                self.msg.show(FP_MSG_ERROR, "error")
            return
        slot = result.data.get("slot")
        found = self.ctx.users.find_slot(slot)
        if found is None:
            events.warning("fingerprint.deny", f"slot {slot} matched but is not registered")
            self.msg.show(FP_MSG_DENIED, "error")
            return
        user, _ = found
        if not user.enabled:
            events.warning("fingerprint.deny", f"{user.name} is disabled (slot {slot})")
            self.msg.show(f"Access for {user.name} is disabled.", "error")
            return
        if self.ctx.lock.unlock(reason=f"fingerprint {user.name} slot {slot}"):
            self.msg.show(f"Welcome, {user.name}", "ok")
        else:
            self.msg.show("Relay fault — door not opened.", "error")

    # -------------------------------------------------------------------- card
    def _card(self, uid: bytes) -> None:
        """A card was put on the reader."""
        if not self.alive:
            return
        check = self.ctx.cards.check(uid)
        if check.ok:
            name = check.user.name
            if self.ctx.lock.unlock(reason=f"card {name}"):
                self.msg.show(f"Welcome, {name}", "ok")
            else:
                self.msg.show("Relay fault — door not opened.", "error")
        elif check.reason == "disabled":
            self.msg.show(f"Access for {check.user.name} is disabled.", "error")
        elif check.reason == "locked_out":
            self.msg.show(f"Too many unknown cards. Wait {math.ceil(check.wait)} s", "error")
        else:
            n = check.attempt.remaining_attempts if check.attempt else 0
            self.msg.show(f"{CARD_MSG_DENIED} ({n} left)" if n else CARD_MSG_DENIED, "error")

    # -------------------------------------------------------------------- face
    def _face_state(self, state: str) -> None:
        if not self.alive:
            return
        if state in FACE_HINTS:
            self.camera.hint(*FACE_HINTS[state])
        if state == CAMERA_OFFLINE:
            self.camera.clear()

    def _face_match(self, user_id: str, score: float) -> None:
        """The camera worker saw an enrolled face on enough frames in a row."""
        if not self.alive:
            return
        events = self.ctx.events
        user = self.ctx.users.by_id(user_id)
        if user is None:
            events.warning("face.deny", "matched a user who no longer exists")
            self.camera.hint(*FACE_HINTS[FACE_UNKNOWN])
            return
        if not user.enabled:
            events.warning("face.deny", f"{user.name} is disabled")
            self.camera.hint(f"Access for {user.name} is disabled", theme.DANGER_HI)
            return
        if self.ctx.lock.unlock(reason=f"face {user.name} {score:.2f}"):
            self.msg.show(f"Welcome, {user.name}", "ok")
            self.camera.hint(f"Welcome, {user.name}", theme.OK_HI)
        else:
            self.msg.show("Relay fault — door not opened.", "error")

    # -------------------------------------------------------------------- misc
    def _simulate(self) -> None:
        self.ctx.lock.unlock(reason="simulation button")
        self.msg.show("Simulated unlock (no GPIO driven)", "warn")

    def _sign_in(self) -> None:
        self.app.show(LoginScreen)


class LoginScreen(Screen):
    """Pick your name, enter your PIN."""

    def __init__(self, app):
        super().__init__(app)
        self._checking = False
        self._locked_out = False
        self.header("Sign in", back=app.go_home)
        body = tk.Frame(self, bg=theme.BG)
        body.pack(fill="both", expand=True, padx=theme.PAD, pady=(0, theme.PAD))

        left = tk.Frame(body, bg=theme.BG, width=300)
        left.pack(side="left", fill="y")
        left.pack_propagate(False)
        tk.Label(left, text="Who are you?", font=theme.FONT_BODY, bg=theme.BG,
                 fg=theme.MUTED, anchor="w").pack(fill="x")
        self.list = TouchList(left, height=7)
        self.list.pack(fill="both", expand=True, pady=(4, 0))
        people = [u for u in self.ctx.users.enabled_users() if u.has_pin]
        self.list.set_items([(u.id, u.name + ("  ★" if u.is_admin else ""))
                             for u in people] or [(None, "No accounts yet.")])
        if len(people) == 1:
            self.list.listbox.selection_set(0)
        if not self.ctx.users.has_admin():
            TouchButton(left, "New administrator", self._create_admin, kind="primary",
                        font=theme.FONT_BODY_BOLD).pack(fill="x", ipady=10, pady=(6, 0))

        right = tk.Frame(body, bg=theme.BG)
        right.pack(side="left", fill="both", expand=True, padx=(theme.PAD * 2, 0))
        self.msg = self.banner(right)
        self.msg.pack(fill="x")
        self.pad = PinPad(right, self.ctx.config.getint("security", "passcode_max_length", 12),
                          self._submit, on_cancel=app.go_home)
        self.pad.pack(fill="both", expand=True)
        self._tick_lockout()

    def _submit(self, pin: str) -> None:
        if self._checking or not pin:
            return
        user_id = self.list.selected_key()
        if user_id is None:
            self.msg.show("Choose your name first.", "warn")
            return
        if self.ctx.login.lockout_remaining() > 0:
            self._tick_lockout()
            return
        self.pad.clear()
        self._checking = True
        self.pad.set_enabled(False)
        self.msg.show("Checking…", "muted", sticky=True)

        def work():
            ok = self.ctx.users.check_pin(user_id, pin)
            self.app.post(self._checked, user_id, ok)

        threading.Thread(target=work, name="login", daemon=True).start()

    def _checked(self, user_id: str, ok: bool) -> None:
        self._checking = False
        user = self.ctx.users.by_id(user_id)
        name = user.name if user else "?"
        result = self.ctx.login.record(ok, who=name)
        if not ok:
            self.ctx.events.warning("login.deny", f"wrong PIN for {name}")
        if not self.alive:
            return  # the user walked away mid-check; do not sign anyone in
        if ok and user is not None:
            from .admin import MenuScreen

            self.app.sign_in(user)
            self.app.show(MenuScreen)
            return
        self.pad.set_enabled(True)
        if result.locked_out:
            self._tick_lockout()
        else:
            n = result.remaining_attempts
            self.msg.show(f"Wrong PIN — {n} attempt{'s' if n != 1 else ''} left", "error")

    def _create_admin(self) -> None:
        """First administrator: whoever knows the door code may create one.

        They can already open the door, so this grants nothing new, and the
        attempt counts against the keypad's own retry limit.
        """
        app, users, passcode = self.app, self.ctx.users, self.ctx.passcode

        def door_code_ok(code: str):
            result = passcode.verify(code)
            if result.ok:
                return None
            if result.locked_out:
                return f"Too many attempts. Wait {math.ceil(result.lockout_remaining)} s."
            self.ctx.events.warning("keypad.deny", "wrong door code (new administrator)")
            return "Wrong door code."

        def name_ok(name: str):
            if not name:
                return "Enter a name."
            return "That name is already used." if users.by_name(name) else None

        def ask_pin(name: str) -> None:
            app.show(PinPrompt, title=f"PIN for {name}", validate=users.validate_pin,
                     hint="Your personal sign-in PIN, different from the door code.",
                     on_done=lambda pin: create(name, pin), on_cancel=app.go_home)

        def create(name: str, pin: str) -> None:
            from .admin import MenuScreen

            user = users.add_user(name, pin, ROLE_ADMIN)
            app.sign_in(user)
            app.show(MenuScreen, notice=(f"Administrator {name} created.", "ok"))

        app.show(PinPrompt, title="Door code", confirm=False, validate=door_code_ok,
                 hint="Enter the door code to create the first administrator.",
                 on_done=lambda _code: app.show(
                     TextPrompt, title="Administrator name", validate=name_ok,
                     on_done=ask_pin, on_cancel=app.go_home),
                 on_cancel=app.go_home)

    def _tick_lockout(self) -> None:
        if not self.alive:
            return
        remaining = self.ctx.login.lockout_remaining()
        if remaining > 0:
            self._locked_out = True
            self.pad.set_enabled(False)
            self.msg.show(f"Too many attempts. Wait {math.ceil(remaining)} s",
                          "error", sticky=True)
            self.after(1000, self._tick_lockout)
        elif self._locked_out and not self._checking:
            self._locked_out = False
            self.pad.set_enabled(True)
            self.msg.clear()


class SetupScreen(Screen):
    """First run, nothing configured yet: choose the door code."""

    def __init__(self, app):
        super().__init__(app)
        ctx = self.ctx
        self.header("First-time setup")
        lock = ctx.lock.backend
        pin = ctx.config.getint("lock", "relay_pin", 17)
        lines = [
            "Choose the door code: the digits typed on the keypad, followed by #, "
            "to open the door.",
            "",
            "Relay output:  " + ("simulation (no GPIO is driven)" if lock.simulated
                                 else f"{lock.name} on GPIO{pin}"),
            f"Open time:     {ctx.config.unlock_seconds:g} s",
            "",
            "An administrator account (to change the code, add users or test the "
            "relay) can be added later from Sign in.",
        ]
        tk.Label(self, text="\n".join(lines), font=theme.FONT_BODY, bg=theme.SURFACE,
                 fg=theme.TEXT, justify="left", anchor="nw", wraplength=740,
                 padx=16, pady=14).pack(fill="both", expand=True, padx=theme.PAD)
        TouchButton(self, "Choose the door code", self._start, kind="primary").pack(
            fill="x", padx=theme.PAD, pady=theme.PAD, ipady=14)

    def _start(self) -> None:
        app, passcode = self.app, self.ctx.passcode

        def set_code(code: str) -> None:
            passcode.set_passcode(code)
            app.go_home(notice=("Door code saved. Type it and press # to open.", "ok"))

        app.show(PinPrompt, title="Door code", validate=passcode.validate_new,
                 hint="Digits only. You will type it twice.",
                 on_done=set_code, on_cancel=app.go_home)
