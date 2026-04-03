"""Manages Windows Registry autostart via HKCU\\Software\\Microsoft\\Windows\\CurrentVersion\\Run."""

import winreg
import sys
import logging
from pathlib import Path

logger = logging.getLogger(__name__)

RUN_KEY = r"Software\Microsoft\Windows\CurrentVersion\Run"
APP_NAME = "VoiceTranscriptor"


class StartupManager:
    def get_exe_path(self) -> str:
        """Returns the path used to launch this app."""
        # When packaged as .exe (PyInstaller), sys.frozen is set
        if getattr(sys, "frozen", False):
            return sys.executable
        # Running as .py script
        return f'"{sys.executable}" "{Path(sys.argv[0]).resolve()}"'

    def is_enabled(self) -> bool:
        try:
            with winreg.OpenKey(
                winreg.HKEY_CURRENT_USER, RUN_KEY, 0, winreg.KEY_READ
            ) as key:
                winreg.QueryValueEx(key, APP_NAME)
                return True
        except FileNotFoundError:
            return False
        except Exception as e:
            logger.error("Failed to check startup status: %s", e)
            return False

    def enable(self) -> None:
        try:
            exe_path = self.get_exe_path()
            value = f'{exe_path} --minimized'
            with winreg.OpenKey(
                winreg.HKEY_CURRENT_USER, RUN_KEY, 0, winreg.KEY_SET_VALUE
            ) as key:
                winreg.SetValueEx(key, APP_NAME, 0, winreg.REG_SZ, value)
            logger.info("Autostart enabled: %s", value)
        except Exception as e:
            logger.error("Failed to enable autostart: %s", e)

    def disable(self) -> None:
        try:
            with winreg.OpenKey(
                winreg.HKEY_CURRENT_USER, RUN_KEY, 0, winreg.KEY_SET_VALUE
            ) as key:
                winreg.DeleteValue(key, APP_NAME)
            logger.info("Autostart disabled")
        except FileNotFoundError:
            pass
        except Exception as e:
            logger.error("Failed to disable autostart: %s", e)
