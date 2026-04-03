"""Settings dialog built with customtkinter."""

import tkinter as tk
import customtkinter as ctk
import threading
import logging
from typing import Callable, Optional

logger = logging.getLogger(__name__)

ctk.set_appearance_mode("dark")
ctk.set_default_color_theme("blue")


class SettingsWindow:
    def __init__(
        self,
        config_manager,
        startup_manager,
        transcriber,
        hotkey_manager,
        audio_recorder,
        tk_root: tk.Tk,
        on_hotkey_changed: Optional[Callable[[str], None]] = None,
        on_mic_changed: Optional[Callable[[Optional[int]], None]] = None,
    ) -> None:
        self._cfg = config_manager
        self._startup = startup_manager
        self._transcriber = transcriber
        self._hotkey_mgr = hotkey_manager
        self._recorder = audio_recorder
        self._tk_root = tk_root
        self._on_hotkey_changed = on_hotkey_changed
        self._on_mic_changed = on_mic_changed
        self._window: Optional[ctk.CTkToplevel] = None

    def show(self) -> None:
        if self._window and self._window.winfo_exists():
            self._window.lift()
            self._window.focus_force()
            return
        self._build()

    def _build(self) -> None:
        win = ctk.CTkToplevel(self._tk_root)
        win.title("Voice Transcriptor — Settings")
        win.geometry("480x520")
        win.resizable(False, False)
        win.wm_attributes("-topmost", True)
        win.protocol("WM_DELETE_WINDOW", self._close)
        self._window = win

        # ── API Key ──────────────────────────────────────────────────────────
        ctk.CTkLabel(win, text="AssemblyAI API Key", font=ctk.CTkFont(size=13, weight="bold")).pack(
            anchor="w", padx=24, pady=(20, 4)
        )
        key_frame = ctk.CTkFrame(win, fg_color="transparent")
        key_frame.pack(fill="x", padx=24)
        self._key_var = tk.StringVar(value=self._cfg.get("api_key"))
        self._key_entry = ctk.CTkEntry(
            key_frame, textvariable=self._key_var,
            placeholder_text="Paste key from assemblyai.com",
            show="•", width=290, height=36,
        )
        self._key_entry.pack(side="left", padx=(0, 8))
        self._validate_btn = ctk.CTkButton(
            key_frame, text="Validate", width=100, height=36,
            command=self._start_validate,
        )
        self._validate_btn.pack(side="left")
        self._key_status = ctk.CTkLabel(win, text="", font=ctk.CTkFont(size=11))
        self._key_status.pack(anchor="w", padx=24, pady=(2, 0))

        # ── Hotkey ───────────────────────────────────────────────────────────
        ctk.CTkLabel(win, text="Hotkey", font=ctk.CTkFont(size=13, weight="bold")).pack(
            anchor="w", padx=24, pady=(18, 4)
        )
        hk_frame = ctk.CTkFrame(win, fg_color="transparent")
        hk_frame.pack(fill="x", padx=24)
        self._hk_var = tk.StringVar(value=self._cfg.get("hotkey"))
        self._hk_entry = ctk.CTkEntry(
            hk_frame, textvariable=self._hk_var,
            placeholder_text="e.g. ctrl+alt+space",
            width=290, height=36,
        )
        self._hk_entry.pack(side="left", padx=(0, 8))
        self._hk_status = ctk.CTkLabel(win, text="", font=ctk.CTkFont(size=11))
        self._hk_status.pack(anchor="w", padx=24, pady=(2, 0))

        # ── Microphone ───────────────────────────────────────────────────────
        ctk.CTkLabel(win, text="Microphone", font=ctk.CTkFont(size=13, weight="bold")).pack(
            anchor="w", padx=24, pady=(18, 4)
        )
        devices = self._recorder.list_devices()
        device_names = ["Default"] + [d["name"] for d in devices]
        self._device_map = {d["name"]: d["index"] for d in devices}
        saved_idx = self._cfg.get("mic_device_index")
        current_name = "Default"
        for d in devices:
            if d["index"] == saved_idx:
                current_name = d["name"]
                break
        self._mic_var = tk.StringVar(value=current_name)
        ctk.CTkOptionMenu(
            win, variable=self._mic_var,
            values=device_names, width=400, height=36,
        ).pack(anchor="w", padx=24)

        # ── Autostart ────────────────────────────────────────────────────────
        ctk.CTkLabel(win, text="System", font=ctk.CTkFont(size=13, weight="bold")).pack(
            anchor="w", padx=24, pady=(18, 4)
        )
        self._autostart_var = tk.BooleanVar(value=self._startup.is_enabled())
        ctk.CTkCheckBox(
            win,
            text="Launch on Windows startup",
            variable=self._autostart_var,
        ).pack(anchor="w", padx=24)

        # ── Buttons ──────────────────────────────────────────────────────────
        btn_frame = ctk.CTkFrame(win, fg_color="transparent")
        btn_frame.pack(fill="x", padx=24, pady=(28, 20))
        ctk.CTkButton(btn_frame, text="Save", width=140, command=self._save).pack(side="right", padx=(8, 0))
        ctk.CTkButton(
            btn_frame, text="Cancel", width=100,
            fg_color="transparent", border_width=1,
            command=self._close,
        ).pack(side="right")

        win.after(100, win.lift)

    def _start_validate(self) -> None:
        key = self._key_var.get().strip()
        if not key:
            self._key_status.configure(text="Enter a key", text_color="#ff453a")
            return
        self._validate_btn.configure(state="disabled", text="...")
        self._key_status.configure(text="Checking...", text_color="#8e8e93")

        def _worker():
            ok, msg = self._transcriber.validate_api_key(key)
            if self._window and self._window.winfo_exists():
                self._window.after(0, lambda: self._on_validate_done(ok, msg))

        threading.Thread(target=_worker, daemon=True).start()

    def _on_validate_done(self, ok: bool, msg: str) -> None:
        self._validate_btn.configure(state="normal", text="Validate")
        color = "#30d158" if ok else "#ff453a"
        self._key_status.configure(text=msg, text_color=color)

    def _save(self) -> None:
        key = self._key_var.get().strip()
        hotkey = self._hk_var.get().strip()
        mic_name = self._mic_var.get()
        autostart = self._autostart_var.get()

        if not self._hotkey_mgr.validate_hotkey(hotkey):
            self._hk_status.configure(text="Invalid hotkey combination", text_color="#ff453a")
            return
        self._hk_status.configure(text="")

        old_hotkey = self._cfg.get("hotkey")
        old_key = self._cfg.get("api_key")

        self._cfg.set("api_key", key)
        self._cfg.set("hotkey", hotkey)

        if mic_name == "Default":
            self._cfg.set("mic_device_index", None)
            mic_idx = None
        else:
            mic_idx = self._device_map.get(mic_name)
            self._cfg.set("mic_device_index", mic_idx)

        if autostart:
            self._startup.enable()
        else:
            self._startup.disable()

        if key != old_key:
            self._transcriber.update_api_key(key)

        if hotkey != old_hotkey and self._on_hotkey_changed:
            self._on_hotkey_changed(hotkey)

        if self._on_mic_changed:
            self._on_mic_changed(mic_idx)

        logger.info("Settings saved")
        self._close()

    def _close(self) -> None:
        if self._window:
            try:
                self._window.destroy()
            except Exception:
                pass
            self._window = None
