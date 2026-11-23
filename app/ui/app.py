"""Window, screen switching, session and the thread -> Tk bridge.

Tk is not thread-safe.  The lock supervisor and the fingerprint and camera
workers call back from their own threads, so they never touch a widget: they :meth:`App.post`
a callable onto a queue that the Tk main loop drains every 50 ms.
"""

from __future__ import annotations

import queue
import time
import tkinter as tk
from dataclasses import dataclass

from . import theme
from .widgets import StatusBar


@dataclass
class Context:
    """Everything a screen may need, built once by ``main.py``."""

    config: object
    events: object
    passcode: object
    login: object
    lock: object
    users: object
    fingerprints: object
    temp_codes: object
    faces: object
    alerts: object = None
    hazards: object = None
    history: object = None
    doorbell: object = None
    cloud: object = None
    commands: object = None
    cards: object = None


class Banner(tk.Label):
    """One-line message that clears itself after ``ui.message_seconds``."""

    COLOURS = {"info": theme.TEXT, "ok": theme.OK_HI, "error": theme.DANGER_HI,
               "warn": theme.WARN_HI, "muted": theme.MUTED}

    def __init__(self, master, seconds: float, **kw):
        kw.setdefault("font", theme.FONT_BODY_BOLD)
        super().__init__(master, text="", bg=kw.pop("bg", theme.BG), anchor="w",
                         justify="left", **kw)
        self._seconds = seconds
        self._job = None

    def show(self, text: str, kind: str = "info", sticky: bool = False) -> None:
        if self._job:
            self.after_cancel(self._job)
            self._job = None
        self.configure(text=text, fg=self.COLOURS.get(kind, theme.TEXT))
        if text and not sticky:
            self._job = self.after(int(self._seconds * 1000), self.clear)

    def clear(self) -> None:
        self._job = None
        self.configure(text="")


class Screen(tk.Frame):
    """Base class.  A screen is built fresh on every :meth:`App.show`."""

    #: None (anyone), "user" (any signed-in user) or "admin".
    requires: str | None = None

    def __init__(self, app: "App"):
        super().__init__(app.container, bg=theme.BG)
        self.app = app
        self.ctx = app.ctx
        self.alive = True
        #: Set while a sensor operation is running so the idle timer waits.
        self.keep_alive = False

    def destroy(self) -> None:
        self.alive = False
        super().destroy()

    def on_lock_status(self, status) -> None:
        """Called on the Tk thread whenever the lock state moves."""

    def on_alarm(self, alarms: list[str]) -> None:
        """Called on the Tk thread when a fire / smoke alarm starts or ends."""

    def on_doorbell(self, state: str) -> None:
        """Called on the Tk thread when the doorbell is answered or times out
        (states in app/doorbell.py)."""

    def header(self, title: str, back=None, back_text: str = "Back") -> tk.Frame:
        bar = tk.Frame(self, bg=theme.BG)
        bar.pack(fill="x", padx=theme.PAD, pady=(theme.PAD, 4))
        if back:
            from .widgets import TouchButton

            TouchButton(bar, "‹ " + back_text, back, width=7).pack(side="left")
        tk.Label(bar, text=title, font=theme.FONT_TITLE, bg=theme.BG,
                 fg=theme.TEXT).pack(side="left", padx=12)
        return bar

    def banner(self, master=None) -> Banner:
        return Banner(master or self, self.ctx.config.getfloat("ui", "message_seconds", 3.0))


class App:
    def __init__(self, root: tk.Tk, ctx: Context, fullscreen: bool | None = None):
        self.root = root
        self.ctx = ctx
        self.session = None
        self.screen: Screen | None = None
        self._queue: queue.Queue = queue.Queue()
        self._last_activity = time.monotonic()
        self._closing = False

        cfg = ctx.config
        width = cfg.getint("ui", "width", 800)
        height = cfg.getint("ui", "height", 480)
        root.title("Smart Lock")
        root.configure(bg=theme.BG)
        root.geometry(f"{width}x{height}+0+0")
        if fullscreen is None:
            fullscreen = cfg.getbool("ui", "fullscreen", True)
        if fullscreen:
            root.attributes("-fullscreen", True)
            # Some compositors drop a request made before the window is mapped.
            root.after(500, lambda: root.attributes("-fullscreen", True))
        if cfg.getbool("ui", "hide_cursor", True) and fullscreen:
            root.configure(cursor="none")
        if cfg.getbool("ui", "allow_escape", False):
            root.bind("<Escape>", lambda _e: root.attributes("-fullscreen", False))
        root.protocol("WM_DELETE_WINDOW", self.quit)
        root.bind_all("<ButtonPress>", self._touched, add="+")

        self.status_bar = StatusBar(root)
        self.status_bar.pack(fill="x", side="top")
        self.container = tk.Frame(root, bg=theme.BG)
        self.container.pack(fill="both", expand=True)

        # Listen first, so a camera change during start-up is not missed.
        ctx.faces.add_listener(lambda _online: self.post(self._update_badges))
        ctx.fingerprints.add_listener(lambda _online: self.post(self._update_badges))
        if ctx.cards is not None:
            ctx.cards.add_listener(lambda _online: self.post(self._update_badges))
        if ctx.hazards is not None:
            ctx.hazards.add_listener(lambda _states: self.post(self._hazard_changed))
        if ctx.doorbell is not None:
            ctx.doorbell.add_listener(lambda state: self.post(self._doorbell_changed, state))
        self._bot_online = self._bot_state()
        self._update_badges()
        ctx.lock.add_observer(lambda status: self.post(self._lock_changed, status))
        root.after(50, self._drain)
        root.after(1000, self._idle_check)
        self.go_home()

    # --------------------------------------------------------------- threading
    def post(self, fn, *args) -> None:
        """Thread-safe: run ``fn(*args)`` on the Tk thread."""
        self._queue.put((fn, args))

    def _drain(self) -> None:
        while True:
            try:
                fn, args = self._queue.get_nowait()
            except queue.Empty:
                break
            try:
                fn(*args)
            except tk.TclError:
                pass  # the target widget was destroyed meanwhile
        if not self._closing:
            self.root.after(50, self._drain)

    # -------------------------------------------------------------- navigation
    def show(self, screen_cls, **kwargs) -> None:
        needs = getattr(screen_cls, "requires", None)
        if needs and (self.session is None or (needs == "admin" and not self.session.is_admin)):
            self.go_home()
            return
        if self.screen is not None:
            self.screen.destroy()
        self.screen = screen_cls(self, **kwargs)
        self.screen.pack(fill="both", expand=True)
        self.screen.on_lock_status(self.ctx.lock.status())
        if self.ctx.hazards is not None:
            self.screen.on_alarm(self.ctx.hazards.alarms())

    def go_home(self, notice: tuple[str, str] | None = None) -> None:
        from .screens import HomeScreen, SetupScreen

        # Setup only while nothing at all is configured.  With an admin but no
        # door code, Home says so and the admin signs in to set one -- an
        # unauthenticated setup screen would let anyone choose the code.
        if not self.ctx.passcode.is_configured and not self.ctx.users.has_admin():
            self.sign_out(quiet=True)
            self.show(SetupScreen)
        else:
            self.show(HomeScreen, notice=notice)

    # ----------------------------------------------------------------- session
    def sign_in(self, user) -> None:
        self.session = user
        self._last_activity = time.monotonic()
        self.ctx.events.info("session.login", f"{user.name} ({user.role})")

    def sign_out(self, quiet: bool = False, reason: str = "") -> None:
        if self.session is not None and not quiet:
            self.ctx.events.info("session.logout",
                                 self.session.name + (f" ({reason})" if reason else ""))
        self.session = None

    def refresh_session(self) -> None:
        """Re-read the signed-in user after an edit (role, enabled, PIN)."""
        if self.session is None:
            return
        fresh = self.ctx.users.by_id(self.session.id)
        if fresh is None or not fresh.enabled:
            self.sign_out(reason="account removed or disabled")
        else:
            self.session = fresh

    def _touched(self, _event) -> None:
        self._last_activity = time.monotonic()

    def _idle_check(self) -> None:
        from .screens import HomeScreen, SetupScreen

        timeout = self.ctx.config.getfloat("security", "session_timeout_seconds", 120.0)
        idle = time.monotonic() - self._last_activity
        screen = self.screen
        at_rest = isinstance(screen, (HomeScreen, SetupScreen))
        if (timeout > 0 and idle > timeout and not at_rest
                and not (screen is not None and screen.keep_alive)):
            if self.session is not None:
                self.sign_out(reason="idle timeout")
                self.go_home(notice=("Signed out after inactivity.", "muted"))
            else:
                self.go_home()
        bot = self._bot_state()
        if bot != self._bot_online:   # the WhatsApp bot came or went
            self._bot_online = bot
            self._update_badges()
        if not self._closing:
            self.root.after(1000, self._idle_check)

    # ------------------------------------------------------------------ status
    def _lock_changed(self, status) -> None:
        self.status_bar.set_lock(status.is_unlocked, status.seconds_remaining, status.error)
        if self.screen is not None and self.screen.alive:
            self.screen.on_lock_status(status)

    def _hazard_changed(self) -> None:
        self._update_badges()
        if self.screen is not None and self.screen.alive:
            self.screen.on_alarm(self.ctx.hazards.alarms())

    def _doorbell_changed(self, state: str) -> None:
        if self.screen is not None and self.screen.alive:
            self.screen.on_doorbell(state)

    def _bot_state(self) -> bool | None:
        """Is the WhatsApp bot collecting alerts?  None when that is not shown."""
        cfg, alerts = self.ctx.config, self.ctx.alerts
        if (alerts is None or not cfg.getbool("api", "enabled", True)
                or not cfg.getbool("bot", "show_offline_badge", True)):
            return None
        return alerts.bot_online()

    def _update_badges(self) -> None:
        from ..fingerprint import MODE_HARDWARE, MODE_SIMULATED
        from ..nfc import MODE_SIMULATED as CARD_SIM
        from ..hazard import STATE_ALARM, STATE_SUSPECT

        badges = []
        hazards = self.ctx.hazards.states() if self.ctx.hazards is not None else {}
        for kind, state in hazards.items():
            if state == STATE_ALARM:
                badges.append((kind.upper() + "!", theme.DANGER_HI))
            elif state == STATE_SUSPECT:
                badges.append((kind.upper() + " ?", theme.WARN))
        if self._bot_state() is False:
            # Alerts and the doorbell are not reaching WhatsApp right now.
            badges.append(("BOT OFF", theme.WARN))
        if self.ctx.lock.backend.simulated:
            badges.append(("LOCK SIM", theme.WARN))
        mode = self.ctx.fingerprints.mode
        if mode == MODE_SIMULATED:
            badges.append(("FP SIM", theme.WARN))
        elif mode == MODE_HARDWARE:
            badges.append(("FP OFF", theme.DANGER_HI) if self.ctx.fingerprints.online is False
                          else ("FP", theme.OK_HI))
        cards = self.ctx.cards
        if cards is not None and cards.mode == CARD_SIM:
            badges.append(("CARD SIM", theme.WARN))
        elif cards is not None and cards.available:
            badges.append(("CARD OFF", theme.DANGER_HI) if cards.online is False
                          else ("CARD", theme.OK_HI))
        if self.ctx.faces.available:
            badges.append(("CAM", theme.OK_HI) if self.ctx.faces.camera_online
                          else ("CAM OFF", theme.DANGER_HI))
        self.status_bar.set_badges(badges)

    # ---------------------------------------------------------------- shutdown
    def quit(self) -> None:
        if self._closing:
            return
        self._closing = True
        try:
            self.ctx.fingerprints.close()
            if self.ctx.cards is not None:
                self.ctx.cards.close()
            self.ctx.faces.close()
        finally:
            self.ctx.lock.close()
            self.root.destroy()
