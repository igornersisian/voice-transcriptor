"""Global hotkey registration using the keyboard library."""

import keyboard
import logging
from typing import Callable

logger = logging.getLogger(__name__)


class HotkeyManager:
    def __init__(self) -> None:
        self._current_hotkey: str | None = None
        self._callback: Callable | None = None

    def register(self, hotkey: str, callback: Callable) -> bool:
        """Register a global hotkey. Returns False if the hotkey string is invalid."""
        if not self.validate_hotkey(hotkey):
            logger.warning("Invalid hotkey string: %s", hotkey)
            return False
        self.unregister()
        try:
            keyboard.add_hotkey(hotkey, callback, suppress=False)
            self._current_hotkey = hotkey
            self._callback = callback
            logger.info("Hotkey registered: %s", hotkey)
            return True
        except Exception as e:
            logger.error("Failed to register hotkey '%s': %s", hotkey, e)
            return False

    def unregister(self) -> None:
        if self._current_hotkey:
            try:
                keyboard.remove_hotkey(self._current_hotkey)
                logger.info("Hotkey unregistered: %s", self._current_hotkey)
            except Exception as e:
                logger.warning("Failed to unregister hotkey: %s", e)
            self._current_hotkey = None
            self._callback = None

    def update(self, new_hotkey: str, callback: Callable) -> bool:
        """Unregister old hotkey and register new one."""
        return self.register(new_hotkey, callback)

    def validate_hotkey(self, hotkey: str) -> bool:
        """Check if a hotkey string is parseable by the keyboard library."""
        if not hotkey or not hotkey.strip():
            return False
        try:
            keyboard.parse_hotkey(hotkey)
            return True
        except Exception:
            return False

    @property
    def current(self) -> str | None:
        return self._current_hotkey
