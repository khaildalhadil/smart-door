"""Screens behind sign-in: menu, users, fingerprints, faces, event log, system."""

from __future__ import annotations

import tkinter as tk

from ..face import (CAMERA_OFFLINE, FACE_CHECKING, FACE_FAR, FACE_MANY, FACE_NONE,
                    MODEL_ID)
from ..users import ROLE_ADMIN, ROLE_USER
from . import theme
from .app import Screen
from .prompts import ChoicePrompt, ConfirmPrompt, InfoScreen, PinPrompt, TextPrompt
from .widgets import CameraView, TouchButton, TouchList

FINGER_LABELS = [
    ("Right thumb", "right thumb"), ("Right index", "right index"),
    ("Right middle", "right middle"), ("Left thumb", "left thumb"),
    ("Left index", "left index"), ("Left middle", "left middle"),
    ("Other…", None),
]


class BusyScreen(Screen):
    """Shown while a short sensor job runs; no way out but the job finishing."""

    def __init__(self, app, title: str, text: str):
        super().__init__(app)
        self.keep_alive = True
        self.header(title)
        self.text = tk.Label(self, text=text, font=theme.FONT_TITLE, bg=theme.BG,
                             fg=theme.MUTED)
        self.text.pack(expand=True)


def side_column(parent, width: int = 220) -> tk.Frame:
    col = tk.Frame(parent, bg=theme.BG, width=width)
    col.pack(side="left", fill="y", padx=(theme.PAD, 0))
    col.pack_propagate(False)
    return col


def side_button(col, text, command, kind="default") -> TouchButton:
    btn = TouchButton(col, text, command, kind=kind, font=theme.FONT_BODY_BOLD)
    btn.pack(fill="x", ipady=10, pady=(0, 6))
    return btn


# --------------------------------------------------------------- shared flows
def pick_finger_label(app, on_label, on_cancel) -> None:
    def chosen(value):
        if value is None:
            app.show(TextPrompt, title="Finger name", on_done=on_label,
                     on_cancel=on_cancel, validate=lambda v: None if v else "Enter a name.")
        else:
            on_label(value)

    app.show(ChoicePrompt, title="Which finger?", options=FINGER_LABELS,
             on_choose=chosen, on_cancel=on_cancel, columns=3)


def start_enrolment(app, user_id: str, back) -> None:
    """Label picker -> enrolment, or an explanation when there is no sensor."""
    fp = app.ctx.fingerprints
    if not fp.available:
        app.show(InfoScreen, title="Fingerprint sensor", text=fp.status_text(),
                 on_back=lambda: back(None))
        return
    pick_finger_label(
        app,
        lambda label: app.show(EnrollScreen, user_id=user_id, label=label, back=back),
        lambda: back(None),
    )


def remove_fingerprint(app, slot: int, back) -> None:
    """Erase the template from the sensor, then drop the registry record.

    If the sensor cannot be reached the admin may drop the record anyway: a
    template with no record is refused at the door (unregistered slot), and the
    slot is overwritten the next time it is enrolled.
    """
    fp, users = app.ctx.fingerprints, app.ctx.users

    def drop_record():
        users.delete_fingerprint(slot)
        back(("Fingerprint record removed.", "ok"))

    def offer_record_only(reason: str):
        app.show(ConfirmPrompt, title="Could not erase from the sensor",
                 message=f"{reason}\n\nRemove the record anyway? The template stays on "
                         "the sensor, but an unregistered template never opens the door.",
                 yes_text="Remove record", on_yes=drop_record, on_no=lambda: back(None))

    if not fp.available:
        offer_record_only(fp.status_text().splitlines()[0])
        return

    def done(result):
        if result.ok:
            users.delete_fingerprint(slot)
            back((f"Fingerprint in slot {slot} removed.", "ok"))
        else:
            offer_record_only(result.message)

    app.show(BusyScreen, title="Removing fingerprint", text=f"Erasing slot {slot}…")
    if not fp.delete_async(slot, done=lambda r: app.post(done, r)):
        back(("The sensor is busy. Try again.", "warn"))


def start_face_enrolment(app, user_id: str | None, back, new_name: str = "") -> None:
    """Face capture, or an explanation when the camera is not set up.

    With ``user_id=None`` the door-only user ``new_name`` is created once the
    face is captured, so a cancelled capture leaves nothing behind.
    """
    faces = app.ctx.faces
    if not faces.available:
        app.show(InfoScreen, title="Face recognition", text=faces.status_text(),
                 on_back=lambda: back(None))
        return
    app.show(FaceEnrollScreen, user_id=user_id, new_name=new_name, back=back)


def remove_face(app, face_id: str, back) -> None:
    found = app.ctx.users.find_face(face_id)
    if found is None:
        back(None)
        return
    user, _ = found

    def really():
        app.ctx.users.delete_face(face_id)
        back((f"Face removed from {user.name}.", "ok"))

    app.show(ConfirmPrompt, title="Remove face",
             message=f"Remove this face from {user.name}? It will no longer open "
                     "the door.",
             yes_text="Remove", on_yes=really, on_no=lambda: back(None))


# ----------------------------------------------------------------------- menu
class MenuScreen(Screen):
    requires = "user"

    def __init__(self, app, notice: tuple[str, str] | None = None):
        super().__init__(app)
        user = app.session
        bar = self.header(user.name + ("  (admin)" if user.is_admin else ""))
        self.msg = self.banner(bar)
        self.msg.pack(side="right", padx=8)

        go = app.show
        # Fingerprint / face entries only once a sensor or the camera is in use
        # (or records exist).
        sensor_on = self.ctx.fingerprints.available
        faces_on = self.ctx.faces.available
        if user.is_admin:
            tiles = [
                ("Unlock door", "ok", self._unlock),
                ("Users", "default", lambda: go(UsersScreen)),
            ]
            if sensor_on or self.ctx.users.all_fingerprints():
                tiles.append(("Fingerprints", "default", lambda: go(FingerprintsScreen)))
            if faces_on or self.ctx.users.all_faces():
                tiles.append(("Faces", "default", lambda: go(FacesScreen)))
            tiles += [
                ("Door code", "default", self._change_passcode),
                ("Event log", "default", lambda: go(EventLogScreen)),
                ("System", "default", lambda: go(SystemScreen)),
                ("My PIN", "default", self._change_my_pin),
                ("Sign out", "danger", self._sign_out),
            ]
        else:
            tiles = [("Unlock door", "ok", self._unlock)]
            mine = (["fingerprints"] if sensor_on or user.fingerprints else []) + (
                ["face"] if faces_on or user.faces else [])
            if mine:
                tiles.append(("My " + " & ".join(mine), "default",
                              lambda: go(UserScreen, user_id=user.id)))
            tiles += [
                ("Change my PIN", "default", self._change_my_pin),
                ("Sign out", "danger", self._sign_out),
            ]
        columns = 4 if len(tiles) > 6 else (2 if len(tiles) == 4 else 3)

        grid = tk.Frame(self, bg=theme.BG)
        grid.pack(fill="both", expand=True, padx=theme.PAD, pady=(0, theme.PAD))
        for i, (label, kind, cmd) in enumerate(tiles):
            r, c = divmod(i, columns)
            TouchButton(grid, label, cmd, kind=kind, wraplength=170).grid(
                row=r, column=c, sticky="nsew", padx=4, pady=4)
        for c in range(columns):
            grid.columnconfigure(c, weight=1, uniform="c")
        for r in range((len(tiles) + columns - 1) // columns):
            grid.rowconfigure(r, weight=1, uniform="r")
        if notice:
            self.msg.show(*notice)

    def _menu(self) -> None:
        self.app.show(MenuScreen)

    def _unlock(self) -> None:
        if self.ctx.lock.unlock(reason=f"menu ({self.app.session.name})"):
            self.msg.show("Door unlocked", "ok")
        else:
            self.msg.show("Relay fault — see the event log", "error")

    def _sign_out(self) -> None:
        self.app.sign_out()
        self.app.go_home()

    def _change_passcode(self) -> None:
        app, passcode = self.app, self.ctx.passcode

        def done(code):
            passcode.set_passcode(code)
            app.show(MenuScreen, notice=("Door code changed.", "ok"))

        app.show(PinPrompt, title="New door code", validate=passcode.validate_new,
                 hint="Typed on the keypad, then #, to open the door.",
                 on_done=done, on_cancel=self._menu)

    def _change_my_pin(self) -> None:
        app, users = self.app, self.ctx.users
        uid = app.session.id

        def done(pin):
            users.set_pin(uid, pin)
            app.refresh_session()
            app.show(MenuScreen, notice=("Your PIN was changed.", "ok"))

        app.show(PinPrompt, title="New sign-in PIN", validate=users.validate_pin,
                 on_done=done, on_cancel=self._menu)


# ---------------------------------------------------------------------- users
class UsersScreen(Screen):
    requires = "admin"

    def __init__(self, app, notice: tuple[str, str] | None = None):
        super().__init__(app)
        self.header("Users", back=lambda: app.show(MenuScreen))
        body = tk.Frame(self, bg=theme.BG)
        body.pack(fill="both", expand=True, padx=theme.PAD, pady=(0, theme.PAD))
        left = tk.Frame(body, bg=theme.BG)
        left.pack(side="left", fill="both", expand=True)
        self.list = TouchList(left, height=8)
        self.list.pack(fill="both", expand=True)
        self.msg = self.banner(left)
        self.msg.pack(fill="x", pady=(4, 0))

        users = self.ctx.users.all_users()
        sensor_on = self.ctx.fingerprints.available
        faces_on = self.ctx.faces.available
        rows = []
        for u in users:
            n = len(u.fingerprints)
            text = f"{u.name}  ·  {u.role}"
            if not u.has_pin:
                text += " (no PIN)"
            if sensor_on or n:
                text += f"  ·  {n} finger{'s' if n != 1 else ''}"
            if faces_on or u.faces:
                text += f"  ·  {len(u.faces)} face{'s' if len(u.faces) != 1 else ''}"
            if not u.enabled:
                text += "  ·  disabled"
            rows.append((u.id, text))
        self.list.set_items(rows)
        for i, u in enumerate(users):
            if not u.enabled:
                self.list.item_colour(i, theme.MUTED)

        col = side_column(body)
        side_button(col, "Add user", self._add, kind="primary")
        side_button(col, "Open", self._open)
        if notice:
            self.msg.show(*notice)

    def _open(self) -> None:
        uid = self.list.selected_key()
        if uid is None:
            self.msg.show("Select a user first.", "warn")
            return
        self.app.show(UserScreen, user_id=uid)

    def _add(self) -> None:
        app, users = self.app, self.ctx.users
        back = lambda: app.show(UsersScreen)  # noqa: E731

        def name_ok(name):
            if not name:
                return "Enter a name."
            return "That name is already used." if users.by_name(name) else None

        def got_name(name):
            app.show(ChoicePrompt, title=f"Role for {name}",
                     options=[("Administrator", ROLE_ADMIN), ("User", ROLE_USER),
                              ("Door only (no PIN)", None)],
                     note="Administrators manage users and settings. Users can sign "
                          "in to unlock the door"
                          + (" and manage their own fingerprints."
                             if self.ctx.fingerprints.available else ".")
                          + " Door only: no sign-in, just a fingerprint or face.",
                     on_choose=lambda role: got_role(name, role), on_cancel=back)

        def got_role(name, role):
            if role is None:
                create(name, ROLE_USER, None)
                return
            app.show(PinPrompt, title=f"PIN for {name}", validate=users.validate_pin,
                     hint="Used to sign in on this panel.",
                     on_done=lambda pin: create(name, role, pin), on_cancel=back)

        def create(name, role, pin):
            try:
                user = users.add_user(name, pin, role)
            except ValueError as exc:
                app.show(UsersScreen, notice=(str(exc), "error"))
                return
            app.show(UserScreen, user_id=user.id, notice=(f"{name} added.", "ok"))

        app.show(TextPrompt, title="New user's name", validate=name_ok,
                 on_done=got_name, on_cancel=back)


class UserScreen(Screen):
    """One user's details, fingerprints and faces.  Users may open only
    themselves; only an administrator adds faces."""

    requires = "user"

    def __init__(self, app, user_id: str, notice: tuple[str, str] | None = None):
        super().__init__(app)
        self.user_id = user_id
        session = app.session
        self.is_admin = session.is_admin
        user = self.ctx.users.by_id(user_id)
        if user is None or (not self.is_admin and user_id != session.id):
            self.header("User", back=self._back)
            tk.Label(self, text="This user no longer exists.", font=theme.FONT_BODY,
                     bg=theme.BG, fg=theme.MUTED).pack(expand=True)
            return
        self.user = user
        self.header(user.name, back=self._back)
        body = tk.Frame(self, bg=theme.BG)
        body.pack(fill="both", expand=True, padx=theme.PAD, pady=(0, theme.PAD))
        left = tk.Frame(body, bg=theme.BG)
        left.pack(side="left", fill="both", expand=True)
        status = "enabled" if user.enabled else "DISABLED"
        tk.Label(left, text=f"Role: {user.role}{'' if user.has_pin else ' (no PIN)'}    "
                            f"Status: {status}    Since: {user.created[:10]}",
                 font=theme.FONT_SMALL, bg=theme.BG, fg=theme.MUTED, anchor="w").pack(fill="x")
        self.list = TouchList(left, height=6)
        self.list.pack(fill="both", expand=True, pady=(4, 0))
        rows = []
        for fp in sorted(user.fingerprints, key=lambda f: f.slot):
            text = f"#{fp.slot}  {fp.label or 'finger'}  ·  {fp.created[:10]}"
            if fp.orphaned:
                text += "  ·  not on sensor"
            rows.append((("fp", fp.slot), text))
        for face in user.faces:
            rows.append((("face", face.id), f"Face  ·  {face.created[:16]}  ·  "
                                            f"{len(face.samples)} pictures"))
        sensor_on = self.ctx.fingerprints.available
        faces_on = self.ctx.faces.available
        if not rows:
            rows = [(None, "No fingerprint or face enrolled yet." if sensor_on or faces_on
                     else "No fingerprint sensor or camera in use.")]
        self.list.set_items(rows)
        self.msg = self.banner(left)
        self.msg.pack(fill="x", pady=(4, 0))

        col = side_column(body)
        if sensor_on:
            side_button(col, "Add fingerprint", self._enrol, kind="primary")
        if faces_on and self.is_admin:
            side_button(col, "Add face", self._enrol_face, kind="primary")
        if user.fingerprints or user.faces:
            side_button(col, "Remove", self._remove)
        side_button(col, "Change PIN" if user.has_pin else "Set PIN", self._set_pin)
        if self.is_admin:
            side_button(col, "Disable" if user.enabled else "Enable", self._toggle)
            side_button(col, "Delete user", self._delete, kind="danger")
        if notice:
            self.msg.show(*notice)

    # ----------------------------------------------------------------- nav
    def _back(self) -> None:
        self.app.show(UsersScreen if self.is_admin else MenuScreen)

    def _here(self, notice=None) -> None:
        self.app.show(UserScreen, user_id=self.user_id, notice=notice)

    # ------------------------------------------------------------- actions
    def _enrol(self) -> None:
        start_enrolment(self.app, self.user_id, self._here)

    def _enrol_face(self) -> None:
        start_face_enrolment(self.app, self.user_id, self._here)

    def _remove(self) -> None:
        key = self.list.selected_key()
        if key is None:
            self.msg.show("Select a fingerprint or face first.", "warn")
            return
        kind, ident = key
        if kind == "face":
            remove_face(self.app, ident, self._here)
            return
        slot = ident
        self.app.show(ConfirmPrompt, title="Remove fingerprint",
                      message=f"Remove fingerprint #{slot} from {self.user.name}?",
                      yes_text="Remove",
                      on_yes=lambda: remove_fingerprint(self.app, slot, self._here),
                      on_no=self._here)

    def _set_pin(self) -> None:
        app, users, uid = self.app, self.ctx.users, self.user_id

        def done(pin):
            users.set_pin(uid, pin)
            app.refresh_session()
            app.show(UserScreen, user_id=uid, notice=("PIN changed.", "ok"))

        app.show(PinPrompt, title=f"New PIN for {self.user.name}",
                 validate=users.validate_pin, on_done=done, on_cancel=self._here)

    def _toggle(self) -> None:
        try:
            self.ctx.users.set_enabled(self.user_id, not self.user.enabled)
        except ValueError as exc:
            self.msg.show(str(exc), "error")
            return
        self.app.refresh_session()
        if self.app.session is None:
            self.app.go_home(notice=("Your account was disabled.", "warn"))
        else:
            self._here()

    def _delete(self) -> None:
        user = self.user
        n, m = len(user.fingerprints), len(user.faces)
        parts = ([f"{n} fingerprint template{'s' if n != 1 else ''}"] if n else []) + (
            [f"{m} face{'s' if m != 1 else ''}"] if m else [])
        extra = f" and erase {' and '.join(parts)}" if parts else ""
        self.app.show(ConfirmPrompt, title="Delete user",
                      message=f"Delete {user.name}{extra}? This cannot be undone.",
                      yes_text="Delete", on_yes=self._do_delete, on_no=self._here)

    def _do_delete(self) -> None:
        app, users, fp = self.app, self.ctx.users, self.ctx.fingerprints
        user = self.user
        slots = [f.slot for f in user.fingerprints]

        def finish(warning: str = ""):
            try:
                users.delete_user(user.id)
            except ValueError as exc:
                app.show(UserScreen, user_id=user.id, notice=(str(exc), "error"))
                return
            app.refresh_session()
            if app.session is None:
                app.go_home(notice=("Your account was deleted.", "warn"))
            elif warning:
                app.show(UsersScreen, notice=(warning, "warn"))
            else:
                app.show(UsersScreen, notice=(f"{user.name} deleted.", "ok"))

        if not slots or not fp.available:
            # Leftover templates are unregistered, so they are refused at the door.
            finish("" if not slots else "Templates left on the offline sensor.")
            return
        app.show(BusyScreen, title="Deleting user", text="Erasing fingerprints…")
        started = fp.delete_many_async(
            slots, done=lambda r: app.post(finish, "" if r.ok else r.message))
        if not started:
            self._here(("The sensor is busy. Try again.", "warn"))


# --------------------------------------------------------------- fingerprints
class FingerprintsScreen(Screen):
    requires = "admin"

    def __init__(self, app, notice: tuple[str, str] | None = None):
        super().__init__(app)
        self.header("Fingerprints", back=lambda: app.show(MenuScreen))
        fp, users = self.ctx.fingerprints, self.ctx.users
        pairs = users.all_fingerprints()
        unknown = sorted(set(getattr(app, "fp_unregistered", [])) - users.used_slots())

        tk.Label(self, text=f"{fp.status_text().splitlines()[0]}   ·   "
                            f"{len(pairs)} registered",
                 font=theme.FONT_SMALL, bg=theme.BG, fg=theme.MUTED, anchor="w").pack(
            fill="x", padx=theme.PAD + 2)
        body = tk.Frame(self, bg=theme.BG)
        body.pack(fill="both", expand=True, padx=theme.PAD, pady=(4, theme.PAD))
        left = tk.Frame(body, bg=theme.BG)
        left.pack(side="left", fill="both", expand=True)
        self.list = TouchList(left, height=8)
        self.list.pack(fill="both", expand=True)
        self.msg = self.banner(left)
        self.msg.pack(fill="x", pady=(4, 0))

        rows, colours = [], []
        for user, f in pairs:
            text = f"#{f.slot:<4} {user.name}  ·  {f.label or 'finger'}"
            if f.orphaned:
                text += "  ·  not on sensor"
            rows.append((("reg", f.slot), text))
            colours.append(theme.WARN_HI if f.orphaned else None)
        for slot in unknown:
            rows.append((("raw", slot), f"#{slot:<4} unknown template (sensor only)"))
            colours.append(theme.DANGER_HI)
        if not rows:
            rows = [(None, "No fingerprints registered.")]
        self.list.set_items(rows)
        for i, colour in enumerate(colours):
            if colour:
                self.list.item_colour(i, colour)

        col = side_column(body)
        side_button(col, "Enroll", self._enrol, kind="primary")
        side_button(col, "Remove", self._remove)
        side_button(col, "Sync with sensor", self._sync)
        if notice:
            self.msg.show(*notice)

    def _here(self, notice=None) -> None:
        self.app.show(FingerprintsScreen, notice=notice)

    def _enrol(self) -> None:
        app = self.app
        people = self.ctx.users.enabled_users()
        if not people:
            self.msg.show("Add a user first.", "warn")
            return
        app.show(ChoicePrompt, title="Enroll a finger for…",
                 options=[(u.name, u.id) for u in people[:12]], columns=3,
                 on_choose=lambda uid: start_enrolment(app, uid, self._here),
                 on_cancel=self._here)

    def _remove(self) -> None:
        key = self.list.selected_key()
        if key is None:
            self.msg.show("Select a fingerprint first.", "warn")
            return
        kind, slot = key
        if kind == "reg":
            user, f = self.ctx.users.find_slot(slot)
            self.app.show(ConfirmPrompt, title="Remove fingerprint",
                          message=f"Remove #{slot} ({f.label or 'finger'}) from {user.name}?",
                          yes_text="Remove",
                          on_yes=lambda: remove_fingerprint(self.app, slot, self._here),
                          on_no=self._here)
        else:
            self.app.show(ConfirmPrompt, title="Erase unknown template",
                          message=f"Erase the unregistered template in slot {slot} "
                                  "from the sensor?",
                          yes_text="Erase", on_yes=lambda: self._erase_raw(slot),
                          on_no=self._here)

    def _erase_raw(self, slot: int) -> None:
        app = self.app

        def done(result):
            if result.ok:
                app.fp_unregistered = [s for s in getattr(app, "fp_unregistered", [])
                                       if s != slot]
            self._here((result.message, "ok" if result.ok else "error"))

        app.show(BusyScreen, title="Erasing", text=f"Erasing slot {slot}…")
        if not self.ctx.fingerprints.delete_async(slot, done=lambda r: app.post(done, r)):
            self._here(("The sensor is busy. Try again.", "warn"))

    def _sync(self) -> None:
        fp, users, app = self.ctx.fingerprints, self.ctx.users, self.app
        if not fp.available:
            app.show(InfoScreen, title="Fingerprint sensor", text=fp.status_text(),
                     on_back=self._here)
            return

        def done(result):
            if not result.ok:
                self._here((result.message.splitlines()[0], "error"))
                return
            drift = users.reconcile(result.data["slots"])
            app.fp_unregistered = drift["unregistered"]
            if drift["orphaned"] or drift["unregistered"]:
                text = (f"{len(drift['orphaned'])} missing on sensor, "
                        f"{len(drift['unregistered'])} unknown on sensor")
                self._here((text, "warn"))
            else:
                self._here(("Registry and sensor agree.", "ok"))

        app.show(BusyScreen, title="Sync", text="Reading the sensor…")
        if not fp.index_async(done=lambda r: app.post(done, r)):
            self._here(("The sensor is busy. Try again.", "warn"))


class EnrollScreen(Screen):
    requires = "user"

    def __init__(self, app, user_id: str, label: str, back):
        super().__init__(app)
        self.user_id, self.label, self._back = user_id, label, back
        user = self.ctx.users.by_id(user_id)
        self.header(f"Enroll — {user.name if user else '?'}  ({label})")
        self.step = tk.Label(self, font=theme.FONT_BODY, bg=theme.BG, fg=theme.MUTED)
        self.step.pack(pady=(20, 0))
        self.instruction = tk.Label(self, font=theme.FONT_STATE, bg=theme.BG,
                                    fg=theme.TEXT, wraplength=740)
        self.instruction.pack(expand=True)
        self.bar = tk.Canvas(self, height=14, bg=theme.SURFACE, highlightthickness=0)
        self.bar.pack(fill="x", padx=40)
        self.buttons = tk.Frame(self, bg=theme.BG, height=80)
        self.buttons.pack(fill="x", padx=theme.PAD, pady=theme.PAD)
        self.buttons.pack_propagate(False)
        self._start()

    def destroy(self) -> None:
        if self.keep_alive:
            self.ctx.fingerprints.cancel()
        super().destroy()

    def _set_buttons(self, *specs) -> None:
        for child in self.buttons.winfo_children():
            child.destroy()
        for text, cmd, kind in specs:
            TouchButton(self.buttons, text, cmd, kind=kind).pack(
                side="left", fill="both", expand=True, padx=4)

    def _progress(self, step: int, total: int, text: str) -> None:
        if not self.alive:
            return
        self.step.configure(text=f"Step {step} of {total}")
        self.instruction.configure(text=text, fg=theme.TEXT)
        self.bar.update_idletasks()
        width = self.bar.winfo_width()
        self.bar.delete("all")
        self.bar.create_rectangle(0, 0, width * step / total, 14, fill=theme.ACCENT, width=0)

    def _start(self) -> None:
        users, fp = self.ctx.users, self.ctx.fingerprints
        try:
            self.slot = users.next_free_slot()
        except ValueError as exc:
            self._finish_error(str(exc))
            return
        self._set_buttons(("Cancel", self._cancel, "danger"))
        started = fp.enroll_async(
            self.slot,
            progress=lambda s, t, text: self.app.post(self._progress, s, t, text),
            done=lambda r: self.app.post(self._done, r),
        )
        if started:
            self.keep_alive = True
            self._progress(0, 4, "Starting…")
        else:
            self._finish_error("The sensor is busy. Try again.")

    def _cancel(self) -> None:
        self.ctx.fingerprints.cancel()
        self.instruction.configure(text="Cancelling…", fg=theme.MUTED)

    def _done(self, result) -> None:
        self.keep_alive = False
        if not result.ok:
            if self.alive:
                self._finish_error(result.message)
            return
        try:
            self.ctx.users.add_fingerprint(self.user_id, self.slot, self.label)
        except ValueError as exc:
            if self.alive:
                self._finish_error(f"Stored on the sensor but not registered: {exc}")
            return
        if self.alive:
            self._back((f"Fingerprint saved in slot {self.slot}.", "ok"))

    def _finish_error(self, message: str) -> None:
        self.step.configure(text="")
        self.instruction.configure(text=message, fg=theme.DANGER_HI)
        self._set_buttons(("Back", lambda: self._back(None), "default"),
                          ("Try again", self._start, "primary"))


# ---------------------------------------------------------------------- faces
class FacesScreen(Screen):
    requires = "admin"

    def __init__(self, app, notice: tuple[str, str] | None = None):
        super().__init__(app)
        self.header("Faces", back=lambda: app.show(MenuScreen))
        faces, users = self.ctx.faces, self.ctx.users
        pairs = users.all_faces()
        tk.Label(self, text=faces.status_text().splitlines()[0], font=theme.FONT_SMALL,
                 bg=theme.BG, fg=theme.MUTED, anchor="w").pack(fill="x", padx=theme.PAD + 2)
        body = tk.Frame(self, bg=theme.BG)
        body.pack(fill="both", expand=True, padx=theme.PAD, pady=(4, theme.PAD))
        left = tk.Frame(body, bg=theme.BG)
        left.pack(side="left", fill="both", expand=True)
        self.list = TouchList(left, height=8)
        self.list.pack(fill="both", expand=True)
        self.msg = self.banner(left)
        self.msg.pack(fill="x", pady=(4, 0))

        rows, colours = [], []
        for user, face in pairs:
            text = f"{user.name}  ·  {face.created[:16]}  ·  {len(face.samples)} pictures"
            colour = None
            if face.model != MODEL_ID:
                text += "  ·  old model, add again"
                colour = theme.WARN_HI
            elif not user.enabled:
                text += "  ·  user disabled"
                colour = theme.MUTED
            rows.append((face.id, text))
            colours.append(colour)
        if not rows:
            rows = [(None, "No faces enrolled yet.")]
        self.list.set_items(rows)
        for i, colour in enumerate(colours):
            if colour:
                self.list.item_colour(i, colour)

        col = side_column(body)
        side_button(col, "Add face", self._enrol, kind="primary")
        side_button(col, "Remove", self._remove)
        if notice:
            self.msg.show(*notice)

    def _here(self, notice=None) -> None:
        self.app.show(FacesScreen, notice=notice)

    def _enrol(self) -> None:
        app, users = self.app, self.ctx.users
        people = users.enabled_users()

        def name_ok(name):
            if not name:
                return "Enter a name."
            return "That name is already used." if users.by_name(name) else None

        def new_person(name):
            # Door only: no PIN, so this person can never sign in to the panel.
            start_face_enrolment(app, None, self._here, new_name=name)

        def chosen(uid):
            if uid is None:
                app.show(TextPrompt, title="New person's name", validate=name_ok,
                         on_done=new_person, on_cancel=self._here)
            else:
                start_face_enrolment(app, uid, self._here)

        app.show(ChoicePrompt, title="Add a face for…",
                 options=[(u.name, u.id) for u in people[:11]] + [("New person…", None)],
                 columns=3, on_choose=chosen, on_cancel=self._here)

    def _remove(self) -> None:
        face_id = self.list.selected_key()
        if face_id is None:
            self.msg.show("Select a face first.", "warn")
            return
        remove_face(self.app, face_id, self._here)


# Hint under the enrolment picture per face state.
ENROL_HINTS = {
    FACE_NONE: ("Look at the camera", theme.MUTED),
    FACE_FAR: ("Come closer", theme.WARN_HI),
    FACE_MANY: ("Only one person, please", theme.WARN_HI),
    FACE_CHECKING: ("Hold still…", theme.ACCENT_HI),
    CAMERA_OFFLINE: ("Camera offline", theme.DANGER_HI),
}


class FaceEnrollScreen(Screen):
    """Live picture while the camera worker collects a few views of one face."""

    requires = "admin"

    def __init__(self, app, user_id: str | None, back, new_name: str = ""):
        super().__init__(app)
        self.user_id, self._back = user_id, back
        self._token = None
        user = self.ctx.users.by_id(user_id) if user_id else None
        self.name = user.name if user else new_name or "?"
        self.header(f"Add face — {self.name}")
        body = tk.Frame(self, bg=theme.BG)
        body.pack(fill="both", expand=True, padx=theme.PAD, pady=(0, theme.PAD))
        self.camera = CameraView(body, self.ctx.faces, 400, 300, bg=theme.BG)
        self.camera.pack(side="left", anchor="n")
        right = tk.Frame(body, bg=theme.BG)
        right.pack(side="left", fill="both", expand=True, padx=(theme.PAD * 2, 0))
        self.step = tk.Label(right, font=theme.FONT_BODY, bg=theme.BG, fg=theme.MUTED,
                             anchor="w")
        self.step.pack(fill="x", pady=(10, 4))
        self.bar = tk.Canvas(right, height=14, bg=theme.SURFACE, highlightthickness=0)
        self.bar.pack(fill="x")
        self.instruction = tk.Label(
            right, font=theme.FONT_BODY, bg=theme.BG, fg=theme.TEXT, wraplength=320,
            justify="left", anchor="nw",
            text="Stand half a metre from the camera, alone, facing it. Turn your head "
                 "a little between pictures.")
        self.instruction.pack(fill="both", expand=True, pady=(12, 0))
        self.buttons = tk.Frame(right, bg=theme.BG, height=70)
        self.buttons.pack(fill="x", side="bottom")
        self.buttons.pack_propagate(False)
        self._start()

    def destroy(self) -> None:
        if self._token is not None:
            self.ctx.faces.release(self._token)
        super().destroy()

    def _set_buttons(self, *specs) -> None:
        for child in self.buttons.winfo_children():
            child.destroy()
        for text, cmd, kind in specs:
            TouchButton(self.buttons, text, cmd, kind=kind).pack(
                side="left", fill="both", expand=True, padx=4)

    def _start(self) -> None:
        app = self.app
        self._progress(0, max(1, self.ctx.config.getint("face", "samples", 5)))
        self.camera.hint(*ENROL_HINTS[FACE_NONE])
        self.instruction.configure(fg=theme.TEXT)
        self._set_buttons(("Cancel", lambda: self._back(None), "danger"))
        self.keep_alive = True
        self._token = self.ctx.faces.enrol(
            self.user_id,
            on_state=lambda state: app.post(self._state, state),
            on_progress=lambda n, total: app.post(self._progress, n, total),
            on_done=lambda result: app.post(self._done, result),
            preview=self.camera.size,
        )

    def _state(self, state: str) -> None:
        if not self.alive:
            return
        if state in ENROL_HINTS:
            self.camera.hint(*ENROL_HINTS[state])
        if state == CAMERA_OFFLINE:
            self.camera.clear()

    def _progress(self, n: int, total: int) -> None:
        if not self.alive:
            return
        self.step.configure(text=f"Picture {n} of {total}")
        self.bar.update_idletasks()
        width = self.bar.winfo_width()
        self.bar.delete("all")
        self.bar.create_rectangle(0, 0, width * n / total, 14, fill=theme.ACCENT, width=0)

    def _done(self, result) -> None:
        self.keep_alive = False
        if self._token is not None:
            self.ctx.faces.release(self._token)
            self._token = None
        if not self.alive:
            return  # the admin left mid-capture: nothing is saved
        if not result.ok:
            self.camera.hint("", theme.MUTED)
            self.instruction.configure(text=result.message, fg=theme.DANGER_HI)
            self._set_buttons(("Back", lambda: self._back(None), "default"),
                              ("Try again", self._start, "primary"))
            return
        users = self.ctx.users
        try:
            if self.user_id is None:
                self.user_id = users.add_user(self.name, None, ROLE_USER).id
            users.add_face(self.user_id, result.data["samples"], MODEL_ID)
        except ValueError as exc:
            self.instruction.configure(text=str(exc), fg=theme.DANGER_HI)
            self._set_buttons(("Back", lambda: self._back(None), "default"))
            return
        self._back((f"Face saved for {self.name}. The door now opens for it.", "ok"))


# ------------------------------------------------------------------ event log
class EventLogScreen(Screen):
    requires = "admin"

    def __init__(self, app):
        super().__init__(app)
        self.header("Event log", back=lambda: app.show(MenuScreen))
        self.list = TouchList(self, font=theme.FONT_LOG, height=17)
        self.list.pack(fill="both", expand=True, padx=theme.PAD, pady=(0, theme.PAD))
        self._render()
        self._listener = lambda *entry: app.post(self._render)
        self.ctx.events.subscribe(self._listener)

    def destroy(self) -> None:
        self.ctx.events.unsubscribe(self._listener)
        super().destroy()

    def _render(self) -> None:
        if not self.alive:
            return
        entries = self.ctx.events.recent(200)
        self.list.set_items([(i, f"{stamp[5:]}  {event:<20} {message}")
                             for i, (stamp, level, event, message) in enumerate(entries)],
                            keep_selection=False)
        for i, (_, level, _, _) in enumerate(entries):
            if level == "WARNING":
                self.list.item_colour(i, theme.WARN_HI)
            elif level == "ERROR":
                self.list.item_colour(i, theme.DANGER_HI)


# --------------------------------------------------------------------- system
class SystemScreen(Screen):
    requires = "admin"

    def __init__(self, app, notice: tuple[str, str] | None = None):
        super().__init__(app)
        self.header("System", back=lambda: app.show(MenuScreen))
        cfg, ctx = self.ctx.config, self.ctx
        body = tk.Frame(self, bg=theme.BG)
        body.pack(fill="both", expand=True, padx=theme.PAD, pady=(0, theme.PAD))
        left = tk.Frame(body, bg=theme.BG)
        left.pack(side="left", fill="both", expand=True)

        backend = ctx.lock.backend
        pin = cfg.getint("lock", "relay_pin", 17)
        lockout = ctx.passcode.lockout_remaining()
        lines = [
            "Relay:        " + ("simulation (no GPIO)" if backend.simulated
                                  else f"{backend.name} on GPIO{pin}"),
            f"Active-high:  {cfg.getbool('lock', 'relay_active_high', True)}",
            f"Unlock time:  {cfg.unlock_seconds:g} s "
            f"(ceiling {cfg.getfloat('lock', 'max_unlock_seconds', 10):g} s)",
            f"Keypad:       {cfg.getint('security', 'max_attempts', 3)} tries, then "
            f"{cfg.getfloat('security', 'lockout_seconds', 30):g} s lockout",
            f"Lockout now:  {f'{lockout:.0f} s' if lockout else 'none'}",
            f"Fingerprint:  {ctx.fingerprints.status_text().splitlines()[0]}",
            f"Face:         {ctx.faces.status_text().splitlines()[0]}",
            f"Config:       {cfg.path}",
        ]
        tk.Label(left, text="\n".join(lines), font=theme.FONT_LOG, bg=theme.SURFACE,
                 fg=theme.TEXT, justify="left", anchor="nw", wraplength=500,
                 padx=12, pady=10).pack(fill="both", expand=True)
        self.msg = self.banner(left)
        self.msg.pack(fill="x", pady=(4, 0))

        col = side_column(body)
        side_button(col, "Relay test (1 s)", self._relay_test, kind="warn")
        side_button(col, "Lock now", self._lock_now)
        side_button(col, "Reset lockouts", self._reset)
        if ctx.fingerprints.available:
            side_button(col, "Sensor info", self._sensor)
        side_button(col, "Exit app", self._exit, kind="danger")
        if notice:
            self.msg.show(*notice)

    def _here(self, notice=None) -> None:
        self.app.show(SystemScreen, notice=notice)

    def _relay_test(self) -> None:
        def run():
            ok = self.ctx.lock.pulse_test(1.0)
            self._here(("Relay pulsed for 1 s." if ok else "Relay write failed.",
                        "ok" if ok else "error"))

        self.app.show(
            ConfirmPrompt, title="Relay self-test",
            message="The relay switches ON for 1 second; with the lock wired, the door "
                    "unlocks briefly.\n\n"
                    "Nothing clicks? The relay board probably switches on with the other "
                    "signal level: flip relay_active_high in config.ini (true / false) "
                    "and restart. Between unlocks the pin is released either way, so a "
                    "wrong setting never leaves the lock powered.",
            yes_text="Pulse relay", kind="warn", on_yes=run, on_no=self._here)

    def _lock_now(self) -> None:
        self.ctx.lock.lock(reason=f"admin ({self.app.session.name})")
        self.msg.show("Locked.", "ok")

    def _reset(self) -> None:
        self.ctx.passcode.reset()
        self.ctx.login.reset()
        self.ctx.events.info("security.reset", f"lockouts cleared by {self.app.session.name}")
        self._here(("Keypad and sign-in lockouts cleared.", "ok"))

    def _sensor(self) -> None:
        fp, app = self.ctx.fingerprints, self.app
        if not fp.available:
            app.show(InfoScreen, title="Fingerprint sensor", text=fp.status_text(),
                     on_back=self._here)
            return

        def done(result):
            if not result.ok:
                app.show(InfoScreen, title="Fingerprint sensor", text=result.message,
                         on_back=self._here)
                return
            info = result.data.get("info", {})
            lines = [fp.status_text(), ""]
            lines += [f"{k:<16} {v}" for k, v in info.items()]
            lines.append(f"{'templates':<16} {result.data.get('count')}")
            app.show(InfoScreen, title="Fingerprint sensor", text="\n".join(lines),
                     on_back=self._here, mono=True)

        app.show(BusyScreen, title="Fingerprint sensor", text="Connecting…")
        if not fp.connect_async(done=lambda r: app.post(done, r)):
            self._here(("The sensor is busy. Try again.", "warn"))

    def _exit(self) -> None:
        self.app.show(ConfirmPrompt, title="Exit",
                      message="Close the lock application? The door stays locked and "
                              "the keypad is unavailable until it is started again.",
                      yes_text="Exit", on_yes=self._really_exit, on_no=self._here)

    def _really_exit(self) -> None:
        self.ctx.events.info("app.exit", f"closed by {self.app.session.name}")
        self.app.quit()
