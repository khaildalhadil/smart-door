"""Touch-friendly building blocks.

Tk's stock ``Button`` draws focus rings and 3-D borders that look wrong on a
small panel and react on press rather than release, so :class:`TouchButton` is
a ``Label`` that behaves like a button: it highlights on press and fires on
release only if the finger is still on it.
"""

from __future__ import annotations

import math
import time
import tkinter as tk

from . import theme


class TouchButton(tk.Label):
    def __init__(self, master, text: str = "", command=None, kind: str = "default",
                 font=theme.FONT_BUTTON, **kw):
        self._kind = kind
        bg, _, fg = theme.BUTTON_KINDS[kind]
        # No cursor of our own: inherit the root's, which is hidden on the panel.
        super().__init__(
            master, text=text, font=font, bg=bg, fg=fg,
            padx=kw.pop("padx", 10), pady=kw.pop("pady", 6), **kw,
        )
        self.command = command
        self._enabled = True
        self._pressed = False
        self.bind("<ButtonPress-1>", self._on_press)
        self.bind("<ButtonRelease-1>", self._on_release)
        self.bind("<Leave>", self._on_leave)

    def _colours(self):
        return theme.BUTTON_KINDS[self._kind]

    def _on_press(self, _event):
        if not self._enabled:
            return
        self._pressed = True
        self.configure(bg=self._colours()[1])

    def _on_leave(self, _event):
        if self._pressed:
            self._pressed = False
            self.configure(bg=self._colours()[0])

    def _on_release(self, event):
        if not (self._enabled and self._pressed):
            return
        self._pressed = False
        self.configure(bg=self._colours()[0])
        inside = 0 <= event.x < self.winfo_width() and 0 <= event.y < self.winfo_height()
        if inside and self.command:
            self.command()

    def set_kind(self, kind: str) -> None:
        self._kind = kind
        self._apply_state()

    def set_enabled(self, enabled: bool) -> None:
        self._enabled = enabled
        self._apply_state()

    def _apply_state(self):
        bg, _, fg = self._colours()
        if self._enabled:
            self.configure(bg=bg, fg=fg)
        else:
            self.configure(bg=theme.SURFACE, fg=theme.BORDER)


# ------------------------------------------------------------------------ keypad
class Keypad(tk.Frame):
    """Phone-style door keypad: 1-9, Clear, 0 and # (confirm), plus a column with
    delete-one-digit and Cancel."""

    def __init__(self, master, on_digit, on_clear, on_delete, on_enter,
                 on_cancel=None, gap: int = 6):
        super().__init__(master, bg=theme.BG)
        self.buttons: list[TouchButton] = []
        layout = [
            ["1", "2", "3"],
            ["4", "5", "6"],
            ["7", "8", "9"],
            ["CLR", "0", "#"],
        ]
        for r, row in enumerate(layout):
            self.rowconfigure(r, weight=1, uniform="row")
            for c, key in enumerate(row):
                if key == "CLR":
                    btn = TouchButton(self, "Clear", on_clear, font=theme.FONT_BUTTON)
                elif key == "#":
                    btn = self.enter_button = TouchButton(self, "#", on_enter, kind="ok",
                                                          font=theme.FONT_KEY)
                else:
                    btn = TouchButton(self, key, lambda k=key: on_digit(k),
                                      font=theme.FONT_KEY)
                btn.grid(row=r, column=c, sticky="nsew", padx=gap // 2, pady=gap // 2)
                self.buttons.append(btn)
        for c in range(4):
            self.columnconfigure(c, weight=1, uniform="col")

        delete = TouchButton(self, "⌫", on_delete, font=theme.FONT_KEY)
        delete.grid(row=0, column=3, rowspan=2 if on_cancel else 4, sticky="nsew",
                    padx=gap // 2, pady=gap // 2)
        self.buttons.append(delete)
        self.cancel_button = None
        if on_cancel:
            self.cancel_button = TouchButton(self, "Cancel", on_cancel, kind="danger")
            self.cancel_button.grid(row=2, column=3, rowspan=2, sticky="nsew",
                                    padx=gap // 2, pady=gap // 2)
            self.buttons.append(self.cancel_button)

    def set_enabled(self, enabled: bool, keep_cancel: bool = False) -> None:
        for btn in self.buttons:
            btn.set_enabled(enabled or (keep_cancel and btn is self.cancel_button))


class PinPad(tk.Frame):
    """Masked digit display + keypad.  The typed value never reaches a label."""

    def __init__(self, master, max_length: int, on_enter, on_cancel=None,
                 mask: str = "*"):
        super().__init__(master, bg=theme.BG)
        self.max_length = max_length
        self.mask = mask
        self._value = ""
        self._on_enter = on_enter
        self.display = tk.Label(
            self, text="", font=theme.FONT_DISPLAY, bg=theme.SURFACE, fg=theme.TEXT,
            anchor="center", height=1,
        )
        self.display.pack(fill="x", padx=3, pady=(0, 6), ipady=4)
        self.keypad = Keypad(
            self, self._digit, self.clear, self._delete,
            on_enter=self._enter, on_cancel=on_cancel,
        )
        self.keypad.pack(fill="both", expand=True)
        self._render()

    @property
    def value(self) -> str:
        return self._value

    def clear(self) -> None:
        self._value = ""
        self._render()

    def set_enabled(self, enabled: bool) -> None:
        self.keypad.set_enabled(enabled, keep_cancel=True)

    def _digit(self, d: str) -> None:
        if len(self._value) < self.max_length:
            self._value += d
            self._render()

    def _delete(self) -> None:
        self._value = self._value[:-1]
        self._render()

    def _enter(self) -> None:
        self._on_enter(self._value)

    def _render(self) -> None:
        self.display.configure(text=self.mask * len(self._value) or " ")


# -------------------------------------------------------------------- text entry
class OnScreenKeyboard(tk.Frame):
    """Latin QWERTY keyboard for names and labels (the panel has no keyboard)."""

    ROWS = ["1234567890", "qwertyuiop", "asdfghjkl", "zxcvbnm"]

    def __init__(self, master, on_key, on_backspace, on_done, on_cancel):
        super().__init__(master, bg=theme.BG)
        self._on_key = on_key
        self._shift = True  # names start with a capital
        self._letter_buttons: list[tuple[TouchButton, str]] = []
        for r, row in enumerate(self.ROWS):
            line = tk.Frame(self, bg=theme.BG)
            line.pack(fill="both", expand=True)
            if r == 3:
                self.shift_button = TouchButton(line, "⇧", self._toggle_shift,
                                                font=theme.FONT_BUTTON)
                self.shift_button.pack(side="left", fill="both", expand=True, padx=2, pady=2)
            for ch in row:
                btn = TouchButton(line, ch, lambda c=ch: self._press(c),
                                  font=theme.FONT_BUTTON, padx=0)
                btn.pack(side="left", fill="both", expand=True, padx=2, pady=2)
                if ch.isalpha():
                    self._letter_buttons.append((btn, ch))
            if r == 3:
                TouchButton(line, "⌫", on_backspace, font=theme.FONT_BUTTON).pack(
                    side="left", fill="both", expand=True, padx=2, pady=2)
        bottom = tk.Frame(self, bg=theme.BG)
        bottom.pack(fill="both", expand=True)
        TouchButton(bottom, "Cancel", on_cancel, kind="danger").pack(
            side="left", fill="both", expand=True, padx=2, pady=2)
        TouchButton(bottom, "-", lambda: self._press("-")).pack(
            side="left", fill="both", expand=True, padx=2, pady=2)
        TouchButton(bottom, "space", lambda: self._press(" ")).pack(
            side="left", fill="both", expand=True, padx=2, pady=2, ipadx=80)
        TouchButton(bottom, "Done", on_done, kind="ok").pack(
            side="left", fill="both", expand=True, padx=2, pady=2)
        self._render_case()

    def _press(self, ch: str) -> None:
        if ch.isalpha() and self._shift:
            ch = ch.upper()
            self._shift = False
            self._render_case()
        self._on_key(ch)

    def _toggle_shift(self) -> None:
        self._shift = not self._shift
        self._render_case()

    def _render_case(self) -> None:
        for btn, ch in self._letter_buttons:
            btn.configure(text=ch.upper() if self._shift else ch)
        self.shift_button.set_kind("primary" if self._shift else "default")


# ------------------------------------------------------------------------- lists
class TouchList(tk.Frame):
    """Listbox with large rows and explicit scroll buttons.

    Drag-to-scroll on a resistive panel misfires as selection, so scrolling is
    done with the arrow buttons instead.
    """

    def __init__(self, master, on_select=None, font=theme.FONT_LIST, height: int = 6):
        super().__init__(master, bg=theme.BG)
        self._keys: list = []
        self._on_select = on_select
        self.listbox = tk.Listbox(
            self, font=font, height=height, activestyle="none", exportselection=False,
            bg=theme.SURFACE, fg=theme.TEXT, selectbackground=theme.ACCENT,
            selectforeground="#ffffff", highlightthickness=0, bd=0, relief="flat",
        )
        self.listbox.pack(side="left", fill="both", expand=True)
        self.listbox.bind("<<ListboxSelect>>", self._selected)
        side = tk.Frame(self, bg=theme.BG)
        side.pack(side="left", fill="y", padx=(6, 0))
        TouchButton(side, "▲", lambda: self._scroll(-1), width=3).pack(
            fill="both", expand=True, pady=(0, 3))
        TouchButton(side, "▼", lambda: self._scroll(1), width=3).pack(
            fill="both", expand=True, pady=(3, 0))

    def set_items(self, items: list[tuple[object, str]], keep_selection: bool = True) -> None:
        previous = self.selected_key() if keep_selection else None
        self.listbox.delete(0, "end")
        self._keys = [k for k, _ in items]
        for _, text in items:
            self.listbox.insert("end", " " + text)
        if previous is not None and previous in self._keys:
            idx = self._keys.index(previous)
            self.listbox.selection_set(idx)
            self.listbox.see(idx)

    def item_colour(self, index: int, colour: str) -> None:
        self.listbox.itemconfigure(index, fg=colour)

    def selected_key(self):
        sel = self.listbox.curselection()
        if not sel:
            return None
        return self._keys[sel[0]]

    def _selected(self, _event) -> None:
        if self._on_select:
            self._on_select(self.selected_key())

    def _scroll(self, direction: int) -> None:
        page = max(1, int(self.listbox.cget("height")) - 1)
        self.listbox.yview_scroll(direction * page, "units")


# ------------------------------------------------------------------ camera view
class CameraView(tk.Label):
    """Live camera preview from :class:`app.face.FaceService`, with a one-line
    hint under the picture.

    The face worker renders each preview frame as PPM bytes; this only polls
    for a new one, so the Tk thread does no image work beyond the copy.
    """

    POLL_MS = 80

    def __init__(self, master, faces, width: int, height: int, bg=theme.SURFACE):
        self._photo = tk.PhotoImage(width=width, height=height)
        super().__init__(master, image=self._photo, compound="top", text=" ",
                         font=theme.FONT_BODY_BOLD, bg=bg, fg=theme.MUTED)
        self.faces = faces
        self.size = (width, height)
        self._seq = None
        self._colour = theme.MUTED
        self._inverted = False
        self._job = self.after(self.POLL_MS, self._tick)

    def _tick(self) -> None:
        frame = self.faces.preview_frame()
        if frame is not None and frame[0] != self._seq:
            self._seq = frame[0]
            self._photo.configure(data=frame[1], format="PPM")
        self._job = self.after(self.POLL_MS, self._tick)

    def resize(self, width: int, height: int) -> None:
        """Another picture size.  Pass the new ``size`` to the face job started
        afterwards: its frames must match."""
        self._photo.configure(width=width, height=height)
        self.size = (width, height)

    def hint(self, text: str, colour: str = theme.MUTED) -> None:
        self._colour = colour
        self.configure(text=text, fg="#ffffff" if self._inverted else colour)

    def set_background(self, bg: str, inverted: bool = False) -> None:
        """``inverted``: white hint text, for a coloured (unlocked) tile."""
        self._inverted = inverted
        self.configure(bg=bg, fg="#ffffff" if inverted else self._colour)

    def clear(self) -> None:
        """Blank the picture (camera gone)."""
        self._photo.blank()
        self._seq = None

    def destroy(self) -> None:
        if self._job:
            self.after_cancel(self._job)
            self._job = None
        super().destroy()


# ------------------------------------------------------------------- status bar
class StatusBar(tk.Frame):
    """Lock state on the left, badges in the middle, clock on the right."""

    def __init__(self, master):
        super().__init__(master, bg=theme.SURFACE, height=theme.STATUS_HEIGHT)
        self.pack_propagate(False)
        self.state_label = tk.Label(self, font=theme.FONT_BODY_BOLD, bg=theme.SURFACE)
        self.state_label.pack(side="left", padx=12)
        self.clock = tk.Label(self, font=theme.FONT_BODY, bg=theme.SURFACE, fg=theme.MUTED)
        self.clock.pack(side="right", padx=12)
        self.badges = tk.Frame(self, bg=theme.SURFACE)
        self.badges.pack(side="right", padx=4)
        self._tick()

    def set_lock(self, unlocked: bool, seconds: float, error: str = "") -> None:
        if error:
            self.state_label.configure(text="● RELAY FAULT", fg=theme.DANGER_HI)
        elif unlocked:
            self.state_label.configure(text=f"● UNLOCKED  {math.ceil(seconds)}s",
                                       fg=theme.OK_HI)
        else:
            self.state_label.configure(text="● LOCKED", fg=theme.MUTED)

    def set_badges(self, badges: list[tuple[str, str]]) -> None:
        for child in self.badges.winfo_children():
            child.destroy()
        for text, colour in badges:
            tk.Label(self.badges, text=text, font=theme.FONT_SMALL, bg=colour,
                     fg="#111111", padx=6).pack(side="left", padx=3)

    def _tick(self) -> None:
        self.clock.configure(text=time.strftime("%H:%M"))
        self.after(5000, self._tick)
