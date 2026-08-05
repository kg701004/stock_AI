"""Shared visual theme: one color palette + one ttk.Style setup applied at startup.

Before this module, every screen used its own ad-hoc foreground="#555555" /
"#791F1F" strings (grep shows two colors, no ttk.Style anywhere) with no P&L
color coding at all. Centralizing the palette here means a future color
change is a one-file edit, and every screen gets the same look for free.

Taiwan market convention is red = price up / gain, green = price down / loss
-- the OPPOSITE of US convention. Every P&L color in this app follows the
Taiwan convention via pl_color()/GAIN/LOSS below.
"""
from __future__ import annotations

import tkinter as tk
from tkinter import ttk

FONT_FAMILY = "Microsoft JhengHei UI"

GAIN = "#C0392B"      # red: price up / profit (Taiwan convention)
LOSS = "#1E8449"      # green: price down / loss (Taiwan convention)
NEUTRAL = "#444444"
MUTED = "#6B6F76"

ACCENT = "#1F4E79"        # primary brand / action color
ACCENT_ACTIVE = "#173A5C"
ACCENT_LIGHT = "#EAF1F8"
WARNING = "#B8860B"
DANGER = "#C0392B"
DANGER_ACTIVE = "#8E2A20"
SUCCESS = LOSS   # green -- reused for non-P&L "operation succeeded" states (ordinary software convention, not the TW price convention)
ERROR = DANGER   # red -- reused for non-P&L "operation failed" states

BACKGROUND = "#F4F6F8"
SURFACE = "#FFFFFF"
BORDER = "#D8DCE2"
HEADER_BG = "#E7ECF2"
ROW_ALT = "#F1F4F8"
TEXT = "#20242A"


def pl_color(value: float | None) -> str:
    """Taiwan convention: positive is red (gain), negative is green (loss), zero/None is neutral."""
    if value is None or value == 0:
        return NEUTRAL
    return GAIN if value > 0 else LOSS


def apply(root: tk.Misc) -> None:
    """Configure ttk.Style once; call this exactly once at app startup."""
    style = ttk.Style(root)
    try:
        style.theme_use("clam")
    except tk.TclError:
        pass

    try:
        root.configure(background=BACKGROUND)
    except tk.TclError:
        pass

    style.configure(".", font=(FONT_FAMILY, 10), background=BACKGROUND, foreground=TEXT)
    style.configure("TFrame", background=BACKGROUND)
    style.configure("TLabel", background=BACKGROUND, foreground=TEXT)
    style.configure("TLabelframe", background=BACKGROUND, foreground=TEXT, bordercolor=BORDER)
    style.configure("TLabelframe.Label", background=BACKGROUND, foreground=ACCENT, font=(FONT_FAMILY, 10, "bold"))
    style.configure("TCheckbutton", background=BACKGROUND, foreground=TEXT)
    style.configure("TRadiobutton", background=BACKGROUND, foreground=TEXT)
    style.configure("TSeparator", background=BORDER)
    style.configure("TPanedwindow", background=BACKGROUND)
    style.configure("TScale", background=BACKGROUND)
    style.configure("TProgressbar", background=ACCENT, troughcolor=HEADER_BG)

    style.configure("Heading.TLabel", font=(FONT_FAMILY, 12, "bold"), foreground=ACCENT)
    style.configure("Muted.TLabel", foreground=MUTED)
    style.configure("Success.TLabel", foreground=LOSS)
    style.configure("Warning.TLabel", foreground=WARNING)
    style.configure("Danger.TLabel", foreground=DANGER)
    style.configure("Gain.TLabel", foreground=GAIN)
    style.configure("Loss.TLabel", foreground=LOSS)

    style.configure("TNotebook", background=BACKGROUND, borderwidth=0, tabmargins=(6, 8, 6, 0))
    style.configure("TNotebook.Tab", padding=(14, 8), font=(FONT_FAMILY, 10), background=HEADER_BG, foreground=TEXT)
    style.map(
        "TNotebook.Tab",
        background=[("selected", ACCENT)],
        foreground=[("selected", "#FFFFFF")],
    )

    style.configure("TButton", font=(FONT_FAMILY, 10), padding=(10, 5), background=SURFACE, foreground=TEXT)
    style.map("TButton", background=[("active", HEADER_BG)])
    style.configure("Primary.TButton", background=ACCENT, foreground="#FFFFFF")
    style.map("Primary.TButton", background=[("active", ACCENT_ACTIVE), ("disabled", "#9FB4C7")])
    style.configure("Danger.TButton", background=DANGER, foreground="#FFFFFF")
    style.map("Danger.TButton", background=[("active", DANGER_ACTIVE), ("disabled", "#D9A9A2")])

    style.configure("Treeview", background=SURFACE, fieldbackground=SURFACE, foreground=TEXT, rowheight=26, borderwidth=0)
    style.configure("Treeview.Heading", background=HEADER_BG, foreground=ACCENT, font=(FONT_FAMILY, 10, "bold"), relief="flat")
    style.map("Treeview.Heading", background=[("active", HEADER_BG)])
    style.map("Treeview", background=[("selected", ACCENT)], foreground=[("selected", "#FFFFFF")])

    style.configure("TEntry", fieldbackground=SURFACE, foreground=TEXT, bordercolor=BORDER)
    style.configure("TCombobox", fieldbackground=SURFACE, foreground=TEXT, background=SURFACE)
    style.map("TCombobox", fieldbackground=[("readonly", SURFACE)])


def stripe(tree: ttk.Treeview) -> None:
    """Register alternating-row tags; combine with stripe_tag(i) at insert time."""
    tree.tag_configure("odd", background=ROW_ALT)
    tree.tag_configure("even", background=SURFACE)


def stripe_tag(index: int) -> str:
    return "odd" if index % 2 else "even"
