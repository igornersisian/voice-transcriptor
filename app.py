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
import keyboard
from enum import Enum, auto
from typing import Optional
from pathlib import Path
from PIL import Image, ImageDraw
import pystray

from config_manager import ConfigManager, CONFIG_DIR, TMP_DIR
from startup_manager import StartupManager
from hotkey_manager import HotkeyManager
from audio_recorder import AudioRecorder
from transcriber import Transcriber, RealtimeSession
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
        self._rt_session: Optional[RealtimeSession] = None
        self._esc_hook = None

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
            chunk_callback=self._on_audio_chunk,
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
        elif event == "cancel_recording":
            self._handle_cancel_recording()
        elif event == "transcription_done":
            self._on_transcription_done(payload)
        elif event == "live_text":
            self._widget.update_live_text(payload or "")
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

    def _handle_cancel_recording(self) -> None:
        """Handle ESC key — cancel recording, discard everything."""
        if self._state != AppState.RECORDING:
            return

        logger.info("Recording cancelled by ESC")

        # Unregister ESC
        if self._esc_hook:
            try:
                keyboard.unhook(self._esc_hook)
            except Exception:
                pass
            self._esc_hook = None

        # Kill realtime session without using its text
        if self._rt_session:
            try:
                self._rt_session.stop()
            except Exception:
                pass
            self._rt_session = None

        # Stop recorder and discard the file
        wav_path = self._recorder.stop()
        if wav_path:
            try:
                os.unlink(wav_path)
            except Exception:
                pass

        self._state = AppState.IDLE
        self._widget.hide()
        self._set_tray_icon(ICON_IDLE)
        logger.info("-> IDLE (cancelled)")

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

    def _restore_focus_and_paste(self, text: str) -> None:
        """Restore focus and paste in a worker thread using pure Win32 API."""

        def _worker():
            try:
                import pyperclip

                hwnd = self._focused_hwnd
                control = self._focused_control
                logger.info("PASTE START: hwnd=%s control=%s text=%d chars",
                            hwnd, control, len(text))

                # Step 1: clipboard
                pyperclip.copy(text)
                verify = pyperclip.paste()
                logger.info("PASTE [1/4] clipboard set, verified=%s", verify[:30] if verify else "EMPTY")

                if not hwnd:
                    logger.error("PASTE ABORT: no target hwnd")
                    return

                if not user32.IsWindow(hwnd):
                    logger.error("PASTE ABORT: hwnd %s no longer valid", hwnd)
                    return

                # Step 2: bring window to foreground
                fg_before = user32.GetForegroundWindow()
                logger.info("PASTE [2/4] foreground before=%s, target=%s", fg_before, hwnd)

                result = user32.SetForegroundWindow(hwnd)
                logger.info("PASTE [2/4] SetForegroundWindow returned %s", result)
                time.sleep(0.2)

                fg_after = user32.GetForegroundWindow()
                logger.info("PASTE [2/4] foreground after=%s (match=%s)",
                            fg_after, fg_after == hwnd)

                # Step 3: set focus to control
                our_tid = kernel32.GetCurrentThreadId()
                target_tid = user32.GetWindowThreadProcessId(hwnd, None)
                logger.info("PASTE [3/4] our_tid=%s target_tid=%s", our_tid, target_tid)

                attached = False
                if target_tid and target_tid != our_tid:
                    att = user32.AttachThreadInput(our_tid, target_tid, True)
                    attached = att != 0
                    logger.info("PASTE [3/4] AttachThreadInput=%s", att)

                if control and control != hwnd and user32.IsWindow(control):
                    user32.SetFocus(control)
                    logger.info("PASTE [3/4] SetFocus to control %s", control)

                time.sleep(0.1)

                # Step 4: send Ctrl+V via keybd_event
                VK_CONTROL = 0x11
                VK_V = 0x56
                KEYEVENTF_KEYUP = 0x0002

                user32.keybd_event(VK_CONTROL, 0, 0, 0)
                user32.keybd_event(VK_V, 0, 0, 0)
                time.sleep(0.05)
                user32.keybd_event(VK_V, 0, KEYEVENTF_KEYUP, 0)
                user32.keybd_event(VK_CONTROL, 0, KEYEVENTF_KEYUP, 0)
                logger.info("PASTE [4/4] keybd_event Ctrl+V sent")

                if attached:
                    user32.AttachThreadInput(our_tid, target_tid, False)

                logger.info("PASTE DONE")

            except Exception as e:
                logger.exception("PASTE FAILED: %s", e)

        threading.Thread(target=_worker, daemon=True).start()

    # ── Recording ─────────────────────────────────────────────────────────────

    def _start_recording(self, captured_hwnd: int = 0) -> None:
        self._capture_focused_control(captured_hwnd)

        # Start real-time transcription session
        try:
            self._rt_session = self._transcriber.create_realtime_session(
                on_text=self._on_live_text,
                on_error=self._on_rt_error,
            )
            self._rt_session.start()
        except Exception as e:
            logger.warning("Failed to start realtime transcription: %s", e)
            self._rt_session = None

        try:
            self._recorder.start()
        except Exception as e:
            logger.error("Failed to start recording: %s", e)
            if self._rt_session:
                self._rt_session.stop()
                self._rt_session = None
            self._show_error(f"Microphone: {e}")
            return

        # Register ESC to stop recording
        try:
            self._esc_hook = keyboard.on_press_key("esc", self._on_esc_press, suppress=False)
        except Exception as e:
            logger.warning("Failed to register ESC key: %s", e)

        self._state = AppState.RECORDING
        self._widget.set_hotkey_label(self._cfg.get("hotkey"))
        self._widget.show_recording()
        self._set_tray_icon(ICON_RECORDING)
        logger.info("-> RECORDING")

    def _stop_and_process(self) -> None:
        # Unregister ESC
        if self._esc_hook:
            try:
                keyboard.unhook(self._esc_hook)
            except Exception:
                pass
            self._esc_hook = None

        # Get realtime text before stopping
        rt_text = ""
        if self._rt_session:
            try:
                rt_text = self._rt_session.stop()
            except Exception as e:
                logger.warning("Realtime session stop error: %s", e)
            self._rt_session = None

        wav_path = self._recorder.stop()

        if wav_path is None:
            if rt_text:
                # Recording was "too short" but we got realtime text — use it
                logger.info("Recording short/silent but got realtime text: %d chars", len(rt_text))
                self._finish_with_text(rt_text)
                return
            self._show_error("Recording too short or silent")
            return

        if rt_text:
            # We already have realtime text — use it directly, skip batch API
            logger.info("Using realtime text (%d chars), skipping batch transcription", len(rt_text))
            try:
                os.unlink(wav_path)
            except Exception:
                pass
            self._finish_with_text(rt_text)
        else:
            # Realtime failed — fall back to batch transcription
            logger.info("No realtime text, falling back to batch transcription")
            self._state = AppState.PROCESSING
            self._widget.show_processing()
            self._set_tray_icon(ICON_PROCESSING)
            logger.info("-> PROCESSING")
            self._current_wav_path = wav_path
            t = threading.Thread(target=self._transcribe_worker, args=(wav_path,), daemon=True)
            t.start()

    def _finish_with_text(self, text: str) -> None:
        """Finish recording with already-transcribed text (from realtime)."""
        logger.info("Transcription OK (%d chars) via realtime", len(text))
        self._history.add(text, language=None)
        self._restore_focus_and_paste(text)
        self._state = AppState.SHOWING_RESULT
        self._widget.show_success(text)
        self._set_tray_icon(ICON_IDLE)
        self._widget.get_tk_root().after(2500, self._return_to_idle)

    def _on_esc_press(self, event) -> None:
        """Called from keyboard listener thread when ESC is pressed."""
        self._dispatch("cancel_recording")

    def _on_live_text(self, text: str, is_final: bool) -> None:
        """Called from realtime transcription thread with updated text."""
        self._dispatch("live_text", text)

    def _on_rt_error(self, error: str) -> None:
        """Called from realtime transcription thread on error."""
        logger.warning("Realtime transcription error: %s", error)

    def _on_audio_chunk(self, chunk: bytes) -> None:
        """Called from recorder thread — forward audio to realtime session."""
        if self._rt_session:
            self._rt_session.send_audio(chunk)

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

        self._restore_focus_and_paste(text)
        self._state = AppState.SHOWING_RESULT
        self._widget.show_success(text)
        self._set_tray_icon(ICON_IDLE)
        self._widget.get_tk_root().after(2500, self._return_to_idle)

    # ── Paste ─────────────────────────────────────────────────────────────────

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
        if self._esc_hook:
            try:
                keyboard.unhook(self._esc_hook)
            except Exception:
                pass
            self._esc_hook = None
        if self._rt_session:
            try:
                self._rt_session.stop()
            except Exception:
                pass
            self._rt_session = None
        if self._state == AppState.RECORDING:
            try:
                self._recorder.stop()
            except Exception:
                pass
        if self._tray:
            self._tray.stop()
        self._widget.destroy()
