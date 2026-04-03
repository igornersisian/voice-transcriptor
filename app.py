"""
Central orchestrator. State machine: IDLE -> RECORDING -> PROCESSING -> IDLE.
All cross-thread communication goes through self._queue.
"""

import queue
import threading
import time
import os
import logging
import ctypes
import ctypes.wintypes
from enum import Enum, auto
from typing import Optional
from pathlib import Path
from PIL import Image, ImageDraw
import pystray

from config_manager import ConfigManager, CONFIG_DIR, TMP_DIR
from startup_manager import StartupManager
from hotkey_manager import HotkeyManager
from audio_recorder import AudioRecorder
from transcriber import Transcriber
from recording_widget import RecordingWidget
from settings_window import SettingsWindow
from history import HistoryManager
from history_window import HistoryWindow

logger = logging.getLogger(__name__)

user32 = ctypes.windll.user32
kernel32 = ctypes.windll.kernel32

HISTORY_PATH = CONFIG_DIR / "history.json"


class AppState(Enum):
    IDLE = auto()
    RECORDING = auto()
    PROCESSING = auto()
    SHOWING_RESULT = auto()
    SHOWING_ERROR = auto()


def _make_tray_icon(color: tuple, size: int = 64) -> Image.Image:
    img = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    draw = ImageDraw.Draw(img)
    pad = 6
    draw.ellipse([pad, pad, size - pad, size - pad], fill=color)
    return img


ICON_IDLE = _make_tray_icon((80, 80, 80, 255))
ICON_RECORDING = _make_tray_icon((220, 50, 50, 255))
ICON_PROCESSING = _make_tray_icon((10, 132, 255, 255))


class App:
    def __init__(self, minimized: bool = False) -> None:
        self._minimized = minimized
        self._queue: queue.Queue = queue.Queue()
        self._state = AppState.IDLE
        self._focused_hwnd: int = 0
        self._focused_control: int = 0
        self._current_wav_path: Optional[str] = None
        self._tray: Optional[pystray.Icon] = None

        # Components
        self._cfg = ConfigManager()
        self._cfg.load()

        self._startup = StartupManager()
        self._hotkey = HotkeyManager()

        self._widget = RecordingWidget()
        tk_root = self._widget.get_tk_root()

        self._recorder = AudioRecorder(
            tmp_dir=TMP_DIR,
            device_index=self._cfg.get("mic_device_index"),
            min_duration=self._cfg.get("min_recording_seconds"),
            silence_threshold=self._cfg.get("silence_rms_threshold"),
            level_callback=self._on_audio_level,
        )

        self._transcriber = Transcriber(api_key=self._cfg.get("api_key"))

        self._history = HistoryManager(HISTORY_PATH)
        self._history_window = HistoryWindow(self._history, tk_root)

        self._settings = SettingsWindow(
            config_manager=self._cfg,
            startup_manager=self._startup,
            transcriber=self._transcriber,
            hotkey_manager=self._hotkey,
            audio_recorder=self._recorder,
            tk_root=tk_root,
            on_hotkey_changed=self._on_hotkey_changed,
            on_mic_changed=self._on_mic_changed,
        )

    def run(self) -> None:
        self._recorder.cleanup_old_tmp_files()
        self._build_tray()

        tray_thread = threading.Thread(target=self._tray.run, daemon=True)
        tray_thread.start()

        if self._cfg.has_api_key():
            self._hotkey.register(self._cfg.get("hotkey"), self._on_hotkey_press)
        else:
            self._widget.get_tk_root().after(500, self._settings.show)

        self._widget.get_tk_root().after(50, self._poll_queue)

        logger.info("App started, state=IDLE")
        self._widget.run_mainloop()

        self._hotkey.unregister()
        if self._tray:
            self._tray.stop()

    def _build_tray(self) -> None:
        menu = pystray.Menu(
            pystray.MenuItem("Settings", self._tray_open_settings),
            pystray.MenuItem("History", self._tray_open_history),
            pystray.Menu.SEPARATOR,
            pystray.MenuItem("Quit", self._tray_quit),
        )
        self._tray = pystray.Icon(
            "VoiceTranscriptor",
            icon=ICON_IDLE,
            title="Voice Transcriptor",
            menu=menu,
        )

    # ── Event queue ───────────────────────────────────────────────────────────

    def _dispatch(self, event: str, payload=None) -> None:
        self._queue.put((event, payload))

    def _poll_queue(self) -> None:
        try:
            while True:
                event, payload = self._queue.get_nowait()
                self._handle_event(event, payload)
        except queue.Empty:
            pass
        self._widget.get_tk_root().after(50, self._poll_queue)

    def _handle_event(self, event: str, payload) -> None:
        logger.debug("Event: %s  state: %s", event, self._state)
        if event == "hotkey":
            self._handle_hotkey(captured_hwnd=payload or 0)
        elif event == "transcription_done":
            self._on_transcription_done(payload)
        elif event == "transcription_progress":
            pass
        elif event == "open_settings":
            self._settings.show()
        elif event == "open_history":
            self._history_window.show()
        elif event == "quit":
            self._quit()

    # ── Hotkey handler ────────────────────────────────────────────────────────

    def _on_hotkey_press(self) -> None:
        """Called from keyboard listener thread — capture HWND immediately."""
        # Capture the foreground window RIGHT NOW, before any queue delay.
        # GetForegroundWindow is thread-safe and works from any thread.
        hwnd = user32.GetForegroundWindow()
        self._dispatch("hotkey", hwnd)

    def _handle_hotkey(self, captured_hwnd: int = 0) -> None:
        if self._state == AppState.IDLE:
            if not self._cfg.has_api_key():
                self._settings.show()
                return
            self._start_recording(captured_hwnd)
        elif self._state == AppState.RECORDING:
            self._stop_and_process()

    # ── Focus capture ─────────────────────────────────────────────────────────

    def _capture_focused_control(self, hwnd: int = 0) -> None:
        """Save both the foreground window and the specific focused control."""
        # Use the HWND captured at hotkey-press time (no queue delay)
        self._focused_hwnd = hwnd or user32.GetForegroundWindow()
        self._focused_control = 0

        if not self._focused_hwnd:
            return

        try:
            fg_tid = user32.GetWindowThreadProcessId(self._focused_hwnd, None)
            our_tid = kernel32.GetCurrentThreadId()
            if fg_tid and fg_tid != our_tid:
                user32.AttachThreadInput(our_tid, fg_tid, True)
                self._focused_control = user32.GetFocus()
                user32.AttachThreadInput(our_tid, fg_tid, False)
            else:
                self._focused_control = user32.GetFocus()
        except Exception as e:
            logger.warning("Failed to capture focused control: %s", e)

        logger.info("Captured HWND=%s, control=%s", self._focused_hwnd, self._focused_control)

    def _restore_focus(self) -> None:
        """Restore focus to the exact control that was active when recording started."""
        hwnd = self._focused_hwnd
        control = self._focused_control

        if not hwnd:
            return

        try:
            # Check window still exists
            if not user32.IsWindow(hwnd):
                logger.warning("Target window no longer exists")
                return

            our_tid = kernel32.GetCurrentThreadId()
            target_tid = user32.GetWindowThreadProcessId(hwnd, None)

            # Alt-key trick: allows SetForegroundWindow from background process.
            # Windows blocks SetForegroundWindow unless the calling process
            # recently received input — a synthetic Alt press satisfies this.
            KEYEVENTF_KEYUP = 0x0002
            user32.keybd_event(0x12, 0, 0, 0)       # Alt down
            user32.keybd_event(0x12, 0, KEYEVENTF_KEYUP, 0)  # Alt up

            user32.SetForegroundWindow(hwnd)
            user32.BringWindowToTop(hwnd)
            time.sleep(0.1)

            # Now attach and set focus to the specific control
            if control and user32.IsWindow(control):
                attached = False
                if target_tid and target_tid != our_tid:
                    user32.AttachThreadInput(our_tid, target_tid, True)
                    attached = True
                user32.SetFocus(control)
                if attached:
                    user32.AttachThreadInput(our_tid, target_tid, False)

            time.sleep(0.1)
        except Exception as e:
            logger.warning("Failed to restore focus: %s", e)

    # ── Recording ─────────────────────────────────────────────────────────────

    def _start_recording(self, captured_hwnd: int = 0) -> None:
        self._capture_focused_control(captured_hwnd)

        try:
            self._recorder.start()
        except Exception as e:
            logger.error("Failed to start recording: %s", e)
            self._show_error(f"Microphone: {e}")
            return

        self._state = AppState.RECORDING
        self._widget.set_hotkey_label(self._cfg.get("hotkey"))
        self._widget.show_recording()
        self._set_tray_icon(ICON_RECORDING)
        logger.info("-> RECORDING")

    def _stop_and_process(self) -> None:
        self._state = AppState.PROCESSING
        self._widget.show_processing()
        self._set_tray_icon(ICON_PROCESSING)
        logger.info("-> PROCESSING")

        wav_path = self._recorder.stop()

        if wav_path is None:
            self._show_error("Recording too short or silent")
            return

        self._current_wav_path = wav_path
        t = threading.Thread(target=self._transcribe_worker, args=(wav_path,), daemon=True)
        t.start()

    # ── Transcription worker ──────────────────────────────────────────────────

    def _transcribe_worker(self, wav_path: str) -> None:
        def progress(msg: str):
            self._dispatch("transcription_progress", msg)

        result = self._transcriber.transcribe(wav_path, progress_callback=progress)

        try:
            os.unlink(wav_path)
        except Exception:
            pass

        self._dispatch("transcription_done", result)

    def _on_transcription_done(self, result) -> None:
        if result.error:
            logger.warning("Transcription error: %s", result.error)
            self._show_error(result.error)
            return

        text = result.text or ""
        if not text:
            self._show_error("No speech detected")
            return

        logger.info("Transcription OK (%d chars), lang=%s", len(text), result.language_code)

        # Save to history
        self._history.add(text, language=result.language_code)

        self._paste_text(text)
        self._state = AppState.SHOWING_RESULT
        self._widget.show_success(text)
        self._set_tray_icon(ICON_IDLE)
        self._widget.get_tk_root().after(2500, self._return_to_idle)

    # ── Paste ─────────────────────────────────────────────────────────────────

    def _paste_text(self, text: str) -> None:
        import pyperclip
        import pyautogui

        pyperclip.copy(text)
        time.sleep(0.05)

        self._restore_focus()
        pyautogui.hotkey("ctrl", "v")
        logger.info("Pasted text to HWND=%s, control=%s", self._focused_hwnd, self._focused_control)

    # ── Error / idle ──────────────────────────────────────────────────────────

    def _show_error(self, msg: str) -> None:
        self._state = AppState.SHOWING_ERROR
        self._widget.show_error(msg)
        self._set_tray_icon(ICON_IDLE)
        self._widget.get_tk_root().after(2500, self._return_to_idle)

    def _return_to_idle(self) -> None:
        self._state = AppState.IDLE
        self._widget.hide()
        logger.info("-> IDLE")

    # ── Audio level callback (recorder thread) ────────────────────────────────

    def _on_audio_level(self, level: float) -> None:
        self._widget.update_audio_level(level)

    # ── Settings callbacks ────────────────────────────────────────────────────

    def _on_hotkey_changed(self, new_hotkey: str) -> None:
        self._hotkey.update(new_hotkey, self._on_hotkey_press)
        logger.info("Hotkey changed to: %s", new_hotkey)

        if not self._hotkey.current and self._cfg.has_api_key():
            self._hotkey.register(new_hotkey, self._on_hotkey_press)

    def _on_mic_changed(self, device_index: Optional[int]) -> None:
        self._recorder._device_index = device_index
        logger.info("Mic device changed to index=%s", device_index)

    # ── Tray callbacks ────────────────────────────────────────────────────────

    def _tray_open_settings(self, icon, item) -> None:
        self._dispatch("open_settings")

    def _tray_open_history(self, icon, item) -> None:
        self._dispatch("open_history")

    def _tray_quit(self, icon, item) -> None:
        self._dispatch("quit")

    def _set_tray_icon(self, icon_image: Image.Image) -> None:
        if self._tray:
            try:
                self._tray.icon = icon_image
            except Exception:
                pass

    # ── Quit ──────────────────────────────────────────────────────────────────

    def _quit(self) -> None:
        logger.info("Quitting...")
        self._hotkey.unregister()
        if self._state == AppState.RECORDING:
            try:
                self._recorder.stop()
            except Exception:
                pass
        if self._tray:
            self._tray.stop()
        self._widget.destroy()
