"""History window showing recent transcriptions with copy-to-clipboard."""

import tkinter as tk
import customtkinter as ctk
import pyperclip
import logging
from typing import Optional

from history import HistoryManager

logger = logging.getLogger(__name__)


class HistoryWindow:
    def __init__(self, history: HistoryManager, tk_root: tk.Tk) -> None:
        self._history = history
        self._tk_root = tk_root
        self._window: Optional[ctk.CTkToplevel] = None

    def show(self) -> None:
        if self._window and self._window.winfo_exists():
            self._window.lift()
            self._window.focus_force()
            return
        self._build()

    def _build(self) -> None:
        win = ctk.CTkToplevel(self._tk_root)
        win.title("Voice Transcriptor — History")
        win.geometry("560x480")
        win.resizable(True, True)
        win.minsize(400, 300)
        win.wm_attributes("-topmost", True)
        win.protocol("WM_DELETE_WINDOW", self._close)
        self._window = win

        # Header
        header = ctk.CTkFrame(win, fg_color="transparent")
        header.pack(fill="x", padx=16, pady=(12, 4))
        ctk.CTkLabel(
            header, text="Recent Transcriptions",
            font=ctk.CTkFont(size=15, weight="bold"),
        ).pack(side="left")
        ctk.CTkButton(
            header, text="Clear All", width=80, height=28,
            fg_color="#ff453a", hover_color="#cc362e",
            command=self._clear_all,
        ).pack(side="right")

        # Scrollable list
        scroll = ctk.CTkScrollableFrame(win, fg_color="transparent")
        scroll.pack(fill="both", expand=True, padx=16, pady=(4, 16))

        entries = self._history.get_all()
        if not entries:
            ctk.CTkLabel(
                scroll, text="No transcriptions yet",
                text_color="#8e8e93", font=ctk.CTkFont(size=12),
            ).pack(pady=40)
        else:
            for entry in entries:
                self._build_entry_card(scroll, entry)

        win.after(100, win.lift)

    def _build_entry_card(self, parent, entry) -> None:
        card = ctk.CTkFrame(parent, corner_radius=8)
        card.pack(fill="x", pady=4)

        # Top row: time + language + copy button
        top = ctk.CTkFrame(card, fg_color="transparent")
        top.pack(fill="x", padx=12, pady=(8, 2))

        time_text = entry.display_time()
        if entry.language:
            time_text += f"  [{entry.language}]"
        ctk.CTkLabel(
            top, text=time_text,
            text_color="#8e8e93", font=ctk.CTkFont(size=10),
        ).pack(side="left")

        copy_btn = ctk.CTkButton(
            top, text="Copy", width=50, height=22,
            font=ctk.CTkFont(size=10),
            command=lambda t=entry.text: self._copy(t, copy_btn),
        )
        copy_btn.pack(side="right")

        # Text
        preview = entry.text[:200] + "..." if len(entry.text) > 200 else entry.text
        ctk.CTkLabel(
            card, text=preview,
            font=ctk.CTkFont(size=12),
            wraplength=480, justify="left", anchor="w",
        ).pack(fill="x", padx=12, pady=(2, 10))

    def _copy(self, text: str, btn: ctk.CTkButton) -> None:
        pyperclip.copy(text)
        btn.configure(text="Copied!", state="disabled")
        if self._window and self._window.winfo_exists():
            self._window.after(1500, lambda: self._reset_btn(btn))

    def _reset_btn(self, btn: ctk.CTkButton) -> None:
        try:
            if btn.winfo_exists():
                btn.configure(text="Copy", state="normal")
        except Exception:
            pass

    def _clear_all(self) -> None:
        self._history.clear()
        self._close()

    def _close(self) -> None:
        if self._window:
            try:
                self._window.destroy()
            except Exception:
                pass
            self._window = None
