"""Entry point. Single-instance enforcement, logging setup, App launch."""

import sys
import os
import logging
import logging.handlers
import ctypes
from pathlib import Path

ERROR_ALREADY_EXISTS = 183
_MUTEX_HANDLE = None  # keep alive for process lifetime


def setup_logging() -> None:
    log_dir = Path(os.environ.get("APPDATA", "~")) / "VoiceTranscriptor"
    log_dir.mkdir(parents=True, exist_ok=True)
    log_path = log_dir / "app.log"

    fmt = logging.Formatter("%(asctime)s %(levelname)-8s %(name)s: %(message)s")

    file_handler = logging.handlers.RotatingFileHandler(
        log_path, maxBytes=2 * 1024 * 1024, backupCount=2, encoding="utf-8"
    )
    file_handler.setFormatter(fmt)

    handlers: list[logging.Handler] = [file_handler]
    # Also log to console if not frozen (dev mode)
    if not getattr(sys, "frozen", False):
        console = logging.StreamHandler(sys.stdout)
        console.setFormatter(fmt)
        handlers.append(console)

    logging.basicConfig(level=logging.DEBUG, handlers=handlers)


def enforce_single_instance() -> bool:
    """Returns True if this is the only running instance."""
    global _MUTEX_HANDLE
    handle = ctypes.windll.kernel32.CreateMutexW(None, False, "VoiceTranscriptorSingleInstance")
    _MUTEX_HANDLE = handle
    if ctypes.windll.kernel32.GetLastError() == ERROR_ALREADY_EXISTS:
        return False
    return True


def main() -> None:
    setup_logging()
    logger = logging.getLogger("main")

    if not enforce_single_instance():
        import tkinter as tk
        import tkinter.messagebox as mb
        root = tk.Tk()
        root.withdraw()
        mb.showwarning("Voice Transcriptor", "Приложение уже запущено в системном трее.")
        root.destroy()
        sys.exit(0)

    minimized = "--minimized" in sys.argv
    logger.info("Starting Voice Transcriptor (minimized=%s)", minimized)

    try:
        from app import App
        App(minimized=minimized).run()
    except Exception:
        logger.exception("Unhandled exception in App.run()")
        import tkinter as tk
        import tkinter.messagebox as mb
        try:
            root = tk.Tk()
            root.withdraw()
            mb.showerror(
                "Voice Transcriptor — Ошибка",
                "Произошла критическая ошибка. Подробности в app.log",
            )
            root.destroy()
        except Exception:
            pass
    finally:
        if _MUTEX_HANDLE:
            ctypes.windll.kernel32.ReleaseMutex(_MUTEX_HANDLE)


if __name__ == "__main__":
    main()
