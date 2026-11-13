"""Colours, fonts and sizes for the 800x480 touch panel.

Font sizes are negative so Tk treats them as pixels, not points: the panel's
reported DPI is unreliable over HDMI and we want the same layout everywhere.
"""

from __future__ import annotations

FAMILY = "DejaVu Sans"
MONO = "DejaVu Sans Mono"

BG = "#0f1419"
SURFACE = "#1b222c"
SURFACE_HI = "#273140"
BORDER = "#32404f"
TEXT = "#e6edf3"
MUTED = "#8b98a5"

ACCENT = "#2f81f7"
ACCENT_HI = "#4c94f8"
OK = "#2ea043"
OK_HI = "#3fb950"
DANGER = "#da3633"
DANGER_HI = "#f85149"
WARN = "#d29922"
WARN_HI = "#e3b341"

# kind -> (background, pressed background, foreground)
BUTTON_KINDS = {
    "default": (SURFACE_HI, BORDER, TEXT),
    "primary": (ACCENT, ACCENT_HI, "#ffffff"),
    "ok": (OK, OK_HI, "#ffffff"),
    "danger": (DANGER, DANGER_HI, "#ffffff"),
    "warn": (WARN, WARN_HI, "#111111"),
    "ghost": (BG, SURFACE, MUTED),
}

FONT_SMALL = (FAMILY, -14)
FONT_BODY = (FAMILY, -18)
FONT_BODY_BOLD = (FAMILY, -18, "bold")
FONT_TITLE = (FAMILY, -24, "bold")
FONT_BUTTON = (FAMILY, -20, "bold")
FONT_KEY = (FAMILY, -30, "bold")
FONT_DISPLAY = (MONO, -38, "bold")
FONT_STATE = (FAMILY, -34, "bold")
FONT_LIST = (FAMILY, -20)
FONT_LOG = (MONO, -13)

STATUS_HEIGHT = 44
PAD = 8
