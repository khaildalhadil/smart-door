"""Reusable full-screen prompts: text, PIN, choice, confirm, info.

Each one takes plain callbacks so the caller decides where to go next.  A
``validate`` callback returns an error string (shown in red, prompt stays up)
or ``None`` to accept.
"""

from __future__ import annotations

import tkinter as tk

from . import theme
from .app import Screen
from .widgets import OnScreenKeyboard, PinPad, TouchButton


class TextPrompt(Screen):
    def __init__(self, app, title: str, on_done, on_cancel, initial: str = "",
                 validate=None, max_length: int = 24):
        super().__init__(app)
        self._on_done = on_done
        self._validate = validate
        self._max = max_length
        self._value = initial
        self.header(title)
        self.display = tk.Label(self, font=theme.FONT_TITLE, bg=theme.SURFACE,
                                fg=theme.TEXT, anchor="w", padx=12)
        self.display.pack(fill="x", padx=theme.PAD, ipady=6)
        self.msg = self.banner()
        self.msg.pack(fill="x", padx=theme.PAD + 4, pady=2)
        OnScreenKeyboard(self, self._key, self._backspace, self._done, on_cancel).pack(
            fill="both", expand=True, padx=theme.PAD - 2, pady=(0, theme.PAD))
        self._render()

    def _key(self, ch: str) -> None:
        if len(self._value) < self._max:
            self._value += ch
            self._render()

    def _backspace(self) -> None:
        self._value = self._value[:-1]
        self._render()

    def _render(self) -> None:
        self.display.configure(text=self._value + "│")

    def _done(self) -> None:
        value = " ".join(self._value.split())
        err = self._validate(value) if self._validate else None
        if err:
            self.msg.show(err, "error")
            return
        self._on_done(value)


class PinPrompt(Screen):
    """Digit entry, optionally asking twice to confirm a new secret."""

    def __init__(self, app, title: str, on_done, on_cancel, validate=None,
                 confirm: bool = True, hint: str = ""):
        super().__init__(app)
        self._title = title
        self._on_done = on_done
        self._validate = validate
        self._confirm = confirm
        self._first: str | None = None
        self.header(title)
        body = tk.Frame(self, bg=theme.BG)
        body.pack(fill="both", expand=True, padx=theme.PAD, pady=(0, theme.PAD))

        side = tk.Frame(body, bg=theme.BG, width=290)
        side.pack(side="left", fill="y")
        side.pack_propagate(False)
        self.step = tk.Label(side, font=theme.FONT_BODY_BOLD, bg=theme.BG,
                             fg=theme.TEXT, wraplength=270, justify="left", anchor="w")
        self.step.pack(fill="x", pady=(10, 6))
        tk.Label(side, text=hint, font=theme.FONT_SMALL, bg=theme.BG, fg=theme.MUTED,
                 wraplength=270, justify="left", anchor="w").pack(fill="x")
        self.msg = self.banner(side)
        self.msg.configure(wraplength=270)
        self.msg.pack(fill="x", pady=10)

        hi = self.ctx.config.getint("security", "passcode_max_length", 12)
        self.pad = PinPad(body, hi, self._enter, on_cancel)
        self.pad.pack(side="left", fill="both", expand=True, padx=(theme.PAD, 0))
        self._render_step()

    def _render_step(self) -> None:
        if self._first is None:
            self.step.configure(text="Enter the new code." if self._confirm
                                else "Enter the code.")
        else:
            self.step.configure(text="Enter it again to confirm.")

    def _enter(self, value: str) -> None:
        self.pad.clear()
        if not value:
            return
        if self._first is None:
            err = self._validate(value) if self._validate else None
            if err:
                self.msg.show(err, "error", sticky=True)
                return
            if self._confirm:
                self._first = value
                self.msg.clear()
                self._render_step()
                return
            self._on_done(value)
            return
        if value != self._first:
            self._first = None
            self._render_step()
            self.msg.show("The two codes did not match. Start again.", "error", sticky=True)
            return
        self._on_done(value)


class ChoicePrompt(Screen):
    def __init__(self, app, title: str, options: list[tuple[str, object]],
                 on_choose, on_cancel, columns: int = 2, note: str = ""):
        super().__init__(app)
        self.header(title, back=on_cancel)
        if note:
            tk.Label(self, text=note, font=theme.FONT_BODY, bg=theme.BG, fg=theme.MUTED,
                     wraplength=760, justify="left", anchor="w").pack(
                fill="x", padx=theme.PAD + 4)
        grid = tk.Frame(self, bg=theme.BG)
        grid.pack(fill="both", expand=True, padx=theme.PAD, pady=theme.PAD)
        rows = (len(options) + columns - 1) // columns
        for i, (label, value) in enumerate(options):
            r, c = divmod(i, columns)
            TouchButton(grid, label, lambda v=value: on_choose(v)).grid(
                row=r, column=c, sticky="nsew", padx=4, pady=4)
        for c in range(columns):
            grid.columnconfigure(c, weight=1, uniform="c")
        for r in range(rows):
            grid.rowconfigure(r, weight=1, uniform="r")


class ConfirmPrompt(Screen):
    def __init__(self, app, title: str, message: str, on_yes, on_no,
                 yes_text: str = "Yes", no_text: str = "Cancel", kind: str = "danger"):
        super().__init__(app)
        self.header(title)
        tk.Label(self, text=message, font=theme.FONT_BODY, bg=theme.BG, fg=theme.TEXT,
                 wraplength=740, justify="left", anchor="nw").pack(
            fill="both", expand=True, padx=theme.PAD * 3, pady=theme.PAD * 2)
        row = tk.Frame(self, bg=theme.BG, height=84)
        row.pack(fill="x", padx=theme.PAD, pady=theme.PAD)
        row.pack_propagate(False)
        TouchButton(row, no_text, on_no).pack(side="left", fill="both", expand=True, padx=4)
        TouchButton(row, yes_text, on_yes, kind=kind).pack(
            side="left", fill="both", expand=True, padx=4)


class InfoScreen(Screen):
    def __init__(self, app, title: str, text: str, on_back, mono: bool = False):
        super().__init__(app)
        self.header(title, back=on_back)
        tk.Label(self, text=text, font=theme.FONT_LOG if mono else theme.FONT_BODY,
                 bg=theme.SURFACE, fg=theme.TEXT, wraplength=750, justify="left",
                 anchor="nw", padx=14, pady=12).pack(
            fill="both", expand=True, padx=theme.PAD, pady=(0, theme.PAD))
