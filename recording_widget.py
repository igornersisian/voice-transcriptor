"""
Frameless always-on-top floating widget. Non-focus-stealing via WS_EX_NOACTIVATE.
Pill shape achieved via -transparentcolor trick.
"""

import tkinter as tk
import ctypes
import ctypes.wintypes
import random
import logging
from collections import deque
from typing import Optional

logger = logging.getLogger(__name__)

# Win32 constants
GWL_EXSTYLE = -20
WS_EX_NOACTIVATE = 0x08000000
WS_EX_TOOLWINDOW = 0x00000080

# Dimensions
WIDGET_WIDTH = 340
WIDGET_HEIGHT = 60
CORNER_RADIUS = 30

# Colors
TRANSPARENT_KEY = "#010101"
BG_COLOR = "#1c1c1e"
TEXT_COLOR = "#e5e5ea"
TEXT_MUTED = "#8e8e93"
DOT_RED = "#ff3b30"
DOT_RED_DIM = "#7a1c16"
BLUE = "#0a84ff"
GREEN = "#30d158"
ERROR_RED = "#ff453a"
BAR_COLOR = "#0a84ff"
BAR_COLOR_HOT = "#ff3b30"

BAR_COUNT = 9
BAR_MAX_H = 22
BAR_MIN_H = 3
BAR_WIDTH = 8
BAR_GAP = 4


class RecordingWidget:
    def __init__(self) -> None:
        self._root = tk.Tk()
        self._gen = 0  # animation generation counter — prevents stale callbacks
        self._level_deque: deque = deque([0.0], maxlen=1)
        self._bar_levels = [0.0] * BAR_COUNT
        self._dot_on = True
        self._spinner_angle = 0
        self._hotkey_label = "ctrl+alt+space"  # updated dynamically
        self._canvas: Optional[tk.Canvas] = None
        self._build_window()
        self._build_canvas()
        self._apply_no_activate()
        self._hide()

    def _build_window(self) -> None:
        root = self._root
        root.overrideredirect(True)
        root.wm_attributes("-topmost", True)
        root.wm_attributes("-alpha", 0.95)
        root.config(bg=TRANSPARENT_KEY)
        root.wm_attributes("-transparentcolor", TRANSPARENT_KEY)
        sw = root.winfo_screenwidth()
        x = (sw - WIDGET_WIDTH) // 2
        root.geometry(f"{WIDGET_WIDTH}x{WIDGET_HEIGHT}+{x}+12")

    def _build_canvas(self) -> None:
        c = tk.Canvas(
            self._root,
            width=WIDGET_WIDTH,
            height=WIDGET_HEIGHT,
            bg=TRANSPARENT_KEY,
            highlightthickness=0,
            bd=0,
        )
        c.pack()
        self._canvas = c
        self._draw_pill(BG_COLOR)

    def _apply_no_activate(self) -> None:
        self._root.update()
        try:
            hwnd = int(self._root.frame(), 16)
            user32 = ctypes.windll.user32
            old_style = user32.GetWindowLongPtrW(hwnd, GWL_EXSTYLE)
            new_style = old_style | WS_EX_NOACTIVATE | WS_EX_TOOLWINDOW
            user32.SetWindowLongPtrW(hwnd, GWL_EXSTYLE, new_style)
        except Exception as e:
            logger.warning("Could not apply WS_EX_NOACTIVATE: %s", e)

    def _draw_pill(self, fill: str) -> None:
        c = self._canvas
        r = CORNER_RADIUS
        w, h = WIDGET_WIDTH, WIDGET_HEIGHT
        c.delete("pill")
        c.create_arc(0, 0, 2*r, 2*r, start=90, extent=90, fill=fill, outline=fill, tags="pill")
        c.create_arc(w-2*r, 0, w, 2*r, start=0, extent=90, fill=fill, outline=fill, tags="pill")
        c.create_arc(0, h-2*r, 2*r, h, start=180, extent=90, fill=fill, outline=fill, tags="pill")
        c.create_arc(w-2*r, h-2*r, w, h, start=270, extent=90, fill=fill, outline=fill, tags="pill")
        c.create_rectangle(r, 0, w-r, h, fill=fill, outline=fill, tags="pill")
        c.create_rectangle(0, r, r, h-r, fill=fill, outline=fill, tags="pill")
        c.create_rectangle(w-r, r, w, h-r, fill=fill, outline=fill, tags="pill")

    def _next_gen(self) -> int:
        """Increment generation counter. All prior animation callbacks become no-ops."""
        self._gen += 1
        return self._gen

    # ── Public API (thread-safe) ──────────────────────────────────────────────

    def set_hotkey_label(self, hotkey: str) -> None:
        self._hotkey_label = hotkey

    def show_recording(self) -> None:
        self._root.after(0, self._do_show_recording)

    def show_processing(self) -> None:
        self._root.after(0, self._do_show_processing)

    def show_success(self, text: str) -> None:
        self._root.after(0, lambda: self._do_show_result(text, success=True))

    def show_error(self, text: str) -> None:
        self._root.after(0, lambda: self._do_show_result(text, success=False))

    def hide(self) -> None:
        self._root.after(0, self._hide)

    def update_audio_level(self, level: float) -> None:
        self._level_deque.append(level)

    def run_mainloop(self) -> None:
        self._root.mainloop()

    def destroy(self) -> None:
        try:
            self._root.quit()
            self._root.destroy()
        except Exception:
            pass

    def get_tk_root(self) -> tk.Tk:
        return self._root

    # ── Internal state renderers ──────────────────────────────────────────────

    def _hide(self) -> None:
        self._next_gen()
        self._root.withdraw()

    def _do_show_recording(self) -> None:
        gen = self._next_gen()
        self._root.after(10, lambda: self._start_recording_anim(gen))

    def _start_recording_anim(self, gen: int) -> None:
        if gen != self._gen:
            return
        self._root.deiconify()
        self._canvas.delete("all")
        self._draw_pill(BG_COLOR)
        self._dot_on = True
        self._bar_levels = [0.0] * BAR_COUNT
        self._render_recording()
        self._pulse_dot(gen)
        self._animate_bars(gen)

    def _render_recording(self) -> None:
        c = self._canvas
        c.delete("content")
        dot_x, dot_y = 28, WIDGET_HEIGHT // 2
        dot_r = 7
        dot_color = DOT_RED if self._dot_on else DOT_RED_DIM
        c.create_oval(
            dot_x - dot_r, dot_y - dot_r,
            dot_x + dot_r, dot_y + dot_r,
            fill=dot_color, outline="", tags="content"
        )
        c.create_text(
            46, dot_y - 7, anchor="w",
            text="Recording", fill=TEXT_COLOR,
            font=("Segoe UI", 11, "bold"), tags="content"
        )
        hint = f"Press {self._hotkey_label} to stop"
        c.create_text(
            46, dot_y + 7, anchor="w",
            text=hint, fill=TEXT_MUTED,
            font=("Segoe UI", 8), tags="content"
        )
        bar_start_x = WIDGET_WIDTH - (BAR_COUNT * (BAR_WIDTH + BAR_GAP)) - 16
        for i, level in enumerate(self._bar_levels):
            bh = max(BAR_MIN_H, int(level * BAR_MAX_H))
            x0 = bar_start_x + i * (BAR_WIDTH + BAR_GAP)
            y0 = WIDGET_HEIGHT // 2 + BAR_MAX_H // 2 - bh
            y1 = WIDGET_HEIGHT // 2 + BAR_MAX_H // 2
            color = BAR_COLOR_HOT if level > 0.7 else BAR_COLOR
            c.create_rectangle(x0, y0, x0 + BAR_WIDTH, y1, fill=color, outline="", tags="content")

    def _pulse_dot(self, gen: int) -> None:
        if gen != self._gen:
            return
        self._dot_on = not self._dot_on
        self._render_recording()
        self._root.after(500, lambda: self._pulse_dot(gen))

    def _animate_bars(self, gen: int) -> None:
        if gen != self._gen:
            return
        target = self._level_deque[0] if self._level_deque else 0.0
        for i in range(BAR_COUNT):
            variance = random.uniform(0.3, 1.0)
            t = target * variance
            self._bar_levels[i] = self._bar_levels[i] * 0.6 + t * 0.4
        self._render_recording()
        self._root.after(60, lambda: self._animate_bars(gen))

    def _do_show_processing(self) -> None:
        gen = self._next_gen()
        self._root.after(10, lambda: self._start_processing_anim(gen))

    def _start_processing_anim(self, gen: int) -> None:
        if gen != self._gen:
            return
        self._root.deiconify()
        self._canvas.delete("all")
        self._draw_pill(BG_COLOR)
        self._spinner_angle = 0
        self._spin_processing(gen)

    def _spin_processing(self, gen: int) -> None:
        if gen != self._gen:
            return
        c = self._canvas
        c.delete("content")
        cx, cy, r = 28, WIDGET_HEIGHT // 2, 10
        start = self._spinner_angle
        c.create_arc(
            cx - r, cy - r, cx + r, cy + r,
            start=start, extent=270,
            style=tk.ARC, outline=BLUE, width=2, tags="content"
        )
        self._spinner_angle = (self._spinner_angle + 12) % 360
        c.create_text(
            46, cy - 7, anchor="w",
            text="Processing...", fill=TEXT_COLOR,
            font=("Segoe UI", 11, "bold"), tags="content"
        )
        c.create_text(
            46, cy + 7, anchor="w",
            text="Sending to AssemblyAI",
            fill=TEXT_MUTED, font=("Segoe UI", 8), tags="content"
        )
        self._root.after(40, lambda: self._spin_processing(gen))

    def _do_show_result(self, text: str, success: bool) -> None:
        gen = self._next_gen()
        self._root.after(10, lambda: self._render_result(text, success, gen))

    def _render_result(self, text: str, success: bool, gen: int) -> None:
        if gen != self._gen:
            return
        self._root.deiconify()
        c = self._canvas
        c.delete("all")
        self._draw_pill(BG_COLOR)
        icon = "\u2713" if success else "\u2715"
        color = GREEN if success else ERROR_RED
        cy = WIDGET_HEIGHT // 2
        c.create_oval(14, cy - 12, 38, cy + 12, fill=color, outline="")
        c.create_text(26, cy, text=icon, fill="white", font=("Segoe UI", 13, "bold"))
        display = text[:42] + "\u2026" if len(text) > 42 else text
        label = display if success else text
        c.create_text(
            46, cy - 7 if success else cy, anchor="w",
            text=label, fill=TEXT_COLOR,
            font=("Segoe UI", 10, "bold" if success else "normal"),
        )
        if success:
            c.create_text(
                46, cy + 8, anchor="w",
                text="Pasted to clipboard", fill=TEXT_MUTED,
                font=("Segoe UI", 8),
            )
