#!/usr/bin/env python3
"""Calibrate the panel's touch layer so a touch lands under the finger.

Shows a cross on the screen, one at a time: touch the centre of each.  The
tool reads the raw touch coordinates straight from the kernel
(/dev/input/eventN), works out the libinput calibration matrix that maps them
onto the picture (kanshi turns the picture 180 degrees), writes it into
~/.config/labwc/rc.xml and reloads labwc.  Three more crosses then check the
result; if the touches still land elsewhere it corrects the matrix and checks
again.  If it fails, times out or is stopped, rc.xml is put back as it was.

    python3 tools/calibrate_touch.py              # on the panel (uses DISPLAY=:0)
    python3 tools/calibrate_touch.py --no-save    # measure and print only

The door app can keep running: this window opens on top of it.
"""

from __future__ import annotations

import argparse
import fcntl
import os
import queue
import re
import shutil
import struct
import subprocess
import sys
import threading
import time
import tkinter as tk
from datetime import datetime
from pathlib import Path

import numpy as np

DEVICE_NAME = "ADS7846 Touchscreen"
RC_XML = Path.home() / ".config" / "labwc" / "rc.xml"

EV_SYN, EV_KEY, EV_ABS = 0, 1, 3
SYN_REPORT = 0
ABS_X, ABS_Y = 0, 1
BTN_TOUCH = 0x14A
EVENT = struct.Struct("llHHi")  # struct input_event on 64-bit Linux

# Targets as fractions of the screen.  The check points are not symmetric
# about the centre, so a touch layer turned 180 degrees cannot pass them.
CAL_POINTS = [(0.125, 0.125), (0.875, 0.125), (0.875, 0.875), (0.125, 0.875)]
CHECK_POINTS = [(0.25, 0.30), (0.75, 0.40), (0.40, 0.75)]
TOLERANCE_PX = 25      # a check touch may land this far from its cross
ALREADY_OK_PX = 12     # first round this close: leave rc.xml alone
MAX_ROUNDS = 4


# ---------------------------------------------------------------- the maths
def fit_affine(src, dst) -> np.ndarray:
    """Least-squares 3x3 affine matrix taking src points (n x 2) to dst."""
    src = np.asarray(src, float)
    dst = np.asarray(dst, float)
    a = np.column_stack([src, np.ones(len(src))])
    rows, *_ = np.linalg.lstsq(a, dst, rcond=None)  # 3 x 2
    return np.vstack([rows.T, [0.0, 0.0, 1.0]])


def apply(m: np.ndarray, pts) -> np.ndarray:
    pts = np.asarray(pts, float)
    return pts @ m[:2, :2].T + m[:2, 2]


def orientations() -> list[np.ndarray]:
    """The 8 ways a compositor can turn or mirror normalised [0, 1] coords."""
    out = []
    for swap in (False, True):
        for sx in (1, -1):
            for sy in (1, -1):
                lin = np.array([[0, sx], [sy, 0]] if swap else [[sx, 0], [0, sy]], float)
                off = np.array([1.0 if row.sum() < 0 else 0.0 for row in lin])
                out.append(np.vstack([np.column_stack([lin, off]), [0, 0, 1]]))
    return out


def snap_orientation(c: np.ndarray, tol: float = 0.15) -> np.ndarray | None:
    for o in orientations():
        if np.max(np.abs(c - o)) < tol:
            return o
    return None


def parse_matrix(text: str) -> np.ndarray | None:
    m = re.search(r"<calibrationMatrix>\s*([^<]*?)\s*</calibrationMatrix>", text)
    if not m:
        return None
    vals = [float(v) for v in m.group(1).split()]
    if len(vals) != 6:
        raise ValueError(f"calibrationMatrix needs 6 numbers, found {len(vals)}")
    return np.array([vals[:3], vals[3:], [0, 0, 1]], float)


def fmt(v: float) -> str:
    s = f"{v:.4f}".rstrip("0").rstrip(".")
    return "0" if s in ("", "-0") else s


def matrix_text(m: np.ndarray) -> str:
    return " ".join(fmt(v) for v in m[:2].ravel())


def with_matrix(text: str, m: np.ndarray, device: str) -> str:
    """rc.xml text with the touch device's calibrationMatrix set to m."""
    new = matrix_text(m)
    if re.search(r"<calibrationMatrix>", text):
        return re.sub(r"<calibrationMatrix>[^<]*</calibrationMatrix>",
                      f"<calibrationMatrix>{new}</calibrationMatrix>", text, count=1)
    if "<libinput>" in text:
        raise ValueError("rc.xml has a <libinput> block without calibrationMatrix; edit it by hand")
    block = (f"  <libinput>\n    <device category=\"{device}\">\n"
             f"      <calibrationMatrix>{new}</calibrationMatrix>\n"
             f"    </device>\n  </libinput>\n")
    if "</openbox_config>" not in text:
        raise ValueError("rc.xml has no </openbox_config>")
    return text.replace("</openbox_config>", block + "</openbox_config>", 1)


# ------------------------------------------------------------- the hardware
def find_device(name: str) -> str | None:
    text = Path("/proc/bus/input/devices").read_text()
    for block in text.split("\n\n"):
        if f'N: Name="{name}"' in block:
            m = re.search(r"H: Handlers=.*?\b(event\d+)\b", block)
            if m:
                return "/dev/input/" + m.group(1)
    return None


def abs_range(fd: int, axis: int) -> tuple[int, int]:
    request = (2 << 30) | (24 << 16) | (ord("E") << 8) | (0x40 + axis)  # EVIOCGABS
    _value, lo, hi, *_ = struct.unpack("6i", fcntl.ioctl(fd, request, bytes(24)))
    return lo, hi


class TouchReader(threading.Thread):
    """Puts ("down", t) and ("up", t, [(x, y), ...]) for every raw touch."""

    def __init__(self, path: str, out: queue.Queue):
        super().__init__(daemon=True)
        self.fd = os.open(path, os.O_RDONLY)
        self.xr = abs_range(self.fd, ABS_X)
        self.yr = abs_range(self.fd, ABS_Y)
        self.out = out

    def normalise(self, x: float, y: float) -> tuple[float, float]:
        # The same [0, 1] scale libinput applies its calibration matrix in.
        (x0, x1), (y0, y1) = self.xr, self.yr
        return (x - x0) / (x1 - x0 + 1), (y - y0) / (y1 - y0 + 1)

    def run(self) -> None:
        x = y = None
        down = False
        samples: list[tuple[int, int]] = []
        while True:
            data = os.read(self.fd, EVENT.size * 64)
            for i in range(0, len(data) - EVENT.size + 1, EVENT.size):
                _s, _us, typ, code, val = EVENT.unpack_from(data, i)
                if typ == EV_ABS and code == ABS_X:
                    x = val
                elif typ == EV_ABS and code == ABS_Y:
                    y = val
                elif typ == EV_KEY and code == BTN_TOUCH:
                    if val and not down:
                        down, samples = True, []
                        self.out.put(("down", time.monotonic()))
                    elif not val and down:
                        down = False
                        self.out.put(("up", time.monotonic(), samples))
                elif typ == EV_SYN and code == SYN_REPORT and down and x is not None:
                    samples.append((x, y))


def reload_labwc() -> None:
    subprocess.run(["pkill", "-HUP", "-u", str(os.getuid()), "-x", "labwc"], check=False)


# ------------------------------------------------------------------- the UI
class Calibrator:
    def __init__(self, root: tk.Tk, reader: TouchReader, events: queue.Queue,
                 save: bool, timeout: float):
        self.root, self.reader, self.events = root, reader, events
        self.save, self.deadline = save, time.monotonic() + timeout
        self.exit_code = 2
        self.original = RC_XML.read_text() if RC_XML.exists() else None
        self.current = (parse_matrix(self.original) if self.original else None)
        if self.current is None:
            self.current = np.eye(3)
        self.changed = False
        self.round = 1
        self.targets = CAL_POINTS
        self.index = 0
        self.taken: list[tuple[tuple, tuple | None, tuple]] = []  # raw, landing, target
        self.cal_pairs: list[tuple[tuple, tuple]] = []           # raw, target
        self.solution: np.ndarray | None = None
        self.busy_until = 0.0
        self.press_t = 0.0
        self.pointer: list[tuple[float, float, float]] = []

        root.configure(bg="black", cursor="none")
        root.attributes("-fullscreen", True)
        root.attributes("-topmost", True)
        root.after(500, lambda: root.attributes("-fullscreen", True))
        self.canvas = tk.Canvas(root, bg="black", highlightthickness=0)
        self.canvas.pack(fill="both", expand=True)
        for ev in ("<ButtonPress-1>", "<B1-Motion>", "<ButtonRelease-1>"):
            self.canvas.bind(ev, self._pointer)
        root.after(700, self._draw)
        root.after(20, self._poll)
        root.after(1000, self._keep_on_top)

    # geometry ------------------------------------------------------------
    def _screen(self) -> tuple[int, int, int, int]:
        return (self.root.winfo_rootx(), self.root.winfo_rooty(),
                max(self.root.winfo_width(), 1), max(self.root.winfo_height(), 1))

    def _to_px(self, p) -> tuple[float, float]:
        _x0, _y0, w, h = self._screen()
        return p[0] * w, p[1] * h

    # drawing -------------------------------------------------------------
    def _draw(self, note: str = "") -> None:
        c = self.canvas
        c.delete("all")
        _x0, _y0, w, h = self._screen()
        tx, ty = self._to_px(self.targets[self.index])
        c.create_line(tx - 22, ty, tx + 23, ty, fill="white", width=3)
        c.create_line(tx, ty - 22, tx, ty + 23, fill="white", width=3)
        c.create_oval(tx - 9, ty - 9, tx + 9, ty + 9, outline="#ff5050", width=3)
        stage = "Calibrating" if self.round == 1 else "Checking"
        text = (f"{stage} the touch screen\nTouch the centre of the cross "
                f"({self.index + 1}/{len(self.targets)})")
        if note:
            text += "\n" + note
        c.create_text(w / 2, h / 2, text=text, fill="white",
                      font=("DejaVu Sans", 15, "bold"), justify="center")

    def _message(self, text: str, colour: str) -> None:
        _x0, _y0, w, h = self._screen()
        self.canvas.delete("all")
        self.canvas.create_text(w / 2, h / 2, text=text, fill=colour,
                                font=("DejaVu Sans", 18, "bold"), justify="center")

    def _keep_on_top(self) -> None:
        self.root.attributes("-topmost", True)
        self.root.lift()
        self.root.after(1000, self._keep_on_top)

    # input ---------------------------------------------------------------
    def _pointer(self, e) -> None:
        self.pointer.append((e.x_root, e.y_root, time.monotonic()))

    def _poll(self) -> None:
        if time.monotonic() > self.deadline:
            print("timeout: no change kept", flush=True)
            self._finish(2, "Timed out - touch settings unchanged")
            return
        try:
            while True:
                ev = self.events.get_nowait()
                if ev[0] == "down":
                    self.press_t = ev[1]
                elif time.monotonic() >= self.busy_until:
                    self.busy_until = time.monotonic() + 0.6
                    samples = ev[2]
                    self.root.after(200, lambda s=samples: self._tap(s))
        except queue.Empty:
            pass
        self.root.after(20, self._poll)

    def _tap(self, samples) -> None:
        if len(samples) < 2:
            return
        if len(samples) > 10:
            samples = samples[3:-3]
        rx, ry = np.median(np.asarray(samples, float), axis=0)
        raw = self.reader.normalise(rx, ry)
        x0, y0, w, h = self._screen()
        seen = [(px, py) for px, py, t in self.pointer if t >= self.press_t - 0.05]
        self.pointer.clear()
        landing = None
        if seen:
            lx, ly = np.median(np.asarray(seen, float), axis=0)
            landing = ((lx - x0) / w, (ly - y0) / h)
            self.canvas.create_oval(lx - x0 - 6, ly - y0 - 6, lx - x0 + 6, ly - y0 + 6,
                                    fill="#40d040", outline="")
        target = self.targets[self.index]
        print(f"round {self.round} point {self.index + 1}: target {target} "
              f"raw ({raw[0]:.3f}, {raw[1]:.3f}) "
              f"landed {'(%.3f, %.3f)' % landing if landing else 'unknown'}", flush=True)
        self.taken.append((raw, landing, target))
        self.index += 1
        if self.index < len(self.targets):
            self.root.after(400, self._draw)
        else:
            self.root.after(400, self._end_round)

    # decisions -----------------------------------------------------------
    def _end_round(self) -> None:
        taken, self.taken = self.taken, []
        x0, y0, w, h = self._screen()
        scale = np.array([w, h], float)
        raws = np.array([t[0] for t in taken])
        targets = np.array([t[2] for t in taken])
        landed = [t[1] for t in taken]

        # Raw -> picture, from every touch so far (the raw values never change).
        pairs = self.cal_pairs + [(t[0], t[2]) for t in taken]
        fit = fit_affine([p[0] for p in pairs], [p[1] for p in pairs])
        miss = np.linalg.norm((apply(fit, [p[0] for p in pairs]) -
                               np.array([p[1] for p in pairs])) * scale, axis=1)
        if miss.max() > TOLERANCE_PX:
            print(f"touches disagree by up to {miss.max():.0f} px: repeating the round", flush=True)
            if self.round == 1:
                self.cal_pairs = []
            self._restart_round("One touch was off - again, please")
            return
        self.cal_pairs = pairs
        self.solution = fit

        errors = None
        if all(landed):
            errors = np.linalg.norm((np.array(landed) - targets) * scale, axis=1)
            print(f"round {self.round}: touches landed {np.round(errors).astype(int).tolist()} px "
                  "from the crosses", flush=True)
            if self.round == 1 and errors.max() <= ALREADY_OK_PX:
                print("touch already lands under the finger: nothing to change", flush=True)
                self._finish(0, "Touch already works - nothing changed", keep=True)
                return
            if self.round > 1 and errors.max() <= TOLERANCE_PX:
                print(f"OK: calibrationMatrix {matrix_text(self.current)} kept", flush=True)
                self._finish(0, "Done - touch works", keep=True)
                return
        else:
            print(f"round {self.round}: the window did not see every touch", flush=True)

        # What the compositor does after libinput: landed = C . current . raw
        c = None
        if all(landed) and len(landed) >= 3:
            f = fit_affine(raws, landed)
            c = snap_orientation(f @ np.linalg.inv(self.current))
        if c is None:
            c = np.eye(3)
        proposal = np.linalg.inv(c) @ fit
        print(f"compositor turn {matrix_text(c)}; new calibrationMatrix "
              f"{matrix_text(proposal)}", flush=True)
        if not self.save:
            self._finish(0, "Measured (not saved)\n" + matrix_text(proposal), keep=True)
            return
        if self.round >= MAX_ROUNDS:
            print("could not get the touches to land: settings restored", flush=True)
            self._finish(1, "Could not calibrate - touch settings restored")
            return
        self._write(proposal)
        self.round += 1
        self.targets = CHECK_POINTS
        self._restart_round("", delay=1.2)

    def _restart_round(self, note: str, delay: float = 0.0) -> None:
        self.index = 0
        self.taken = []
        self.busy_until = time.monotonic() + delay
        self.root.after(int(delay * 1000) + 10, lambda: self._draw(note))

    def _write(self, m: np.ndarray) -> None:
        text = RC_XML.read_text()
        if not self.changed:
            backup = RC_XML.with_name("rc.xml.before-calibrate-" +
                                      datetime.now().strftime("%Y%m%d-%H%M%S"))
            shutil.copy2(RC_XML, backup)
            print(f"backup: {backup}", flush=True)
        RC_XML.write_text(with_matrix(text, m, DEVICE_NAME))
        self.current = m
        self.changed = True
        reload_labwc()
        print(f"wrote calibrationMatrix {matrix_text(m)} and reloaded labwc", flush=True)

    def _finish(self, code: int, text: str, keep: bool = False) -> None:
        self.exit_code = code
        if self.changed and not keep:
            self.restore()
        self._message(text, "#40d040" if code == 0 else "#ff7070")
        self.root.after(2500, self.root.destroy)

    def restore(self) -> None:
        if self.changed and self.original is not None:
            RC_XML.write_text(self.original)
            reload_labwc()
            self.changed = False
            print("rc.xml restored", flush=True)


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--device", default=DEVICE_NAME, help="input device name")
    ap.add_argument("--no-save", action="store_true", help="measure and print only")
    ap.add_argument("--timeout", type=float, default=300, help="seconds to wait (default 300)")
    args = ap.parse_args()

    os.environ.setdefault("DISPLAY", ":0")
    path = find_device(args.device)
    if not path:
        print(f'[!] no input device named "{args.device}"')
        return 1
    if not os.access(path, os.R_OK):
        print(f"[!] cannot read {path}: add yourself to the 'input' group and log in again")
        return 1
    events: queue.Queue = queue.Queue()
    reader = TouchReader(path, events)
    print(f"device {path}: x {reader.xr}, y {reader.yr}; rc.xml {RC_XML}", flush=True)
    reader.start()

    root = tk.Tk()
    root.title("Touch calibration")
    cal = Calibrator(root, reader, events, save=not args.no_save, timeout=args.timeout)
    try:
        root.mainloop()
    finally:
        if cal.exit_code != 0:
            cal.restore()
    return cal.exit_code


if __name__ == "__main__":
    sys.exit(main())
