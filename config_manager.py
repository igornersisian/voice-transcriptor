"""Reads/writes JSON config from %APPDATA%\VoiceTranscriptor\config.json."""

import json
import os
import logging
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

CONFIG_DIR = Path(os.environ.get("APPDATA", "~")) / "VoiceTranscriptor"
CONFIG_PATH = CONFIG_DIR / "config.json"
TMP_DIR = CONFIG_DIR / "tmp"

DEFAULTS: dict[str, Any] = {
    "api_key": "",
    "hotkey": "ctrl+alt+space",
    "mic_device_index": None,
    "autostart": False,
    "min_recording_seconds": 0.5,
    "silence_rms_threshold": 150,
}


class ConfigManager:
    def __init__(self) -> None:
        self._data: dict[str, Any] = dict(DEFAULTS)

    def load(self) -> None:
        CONFIG_DIR.mkdir(parents=True, exist_ok=True)
        TMP_DIR.mkdir(parents=True, exist_ok=True)
        if CONFIG_PATH.exists():
            try:
                with open(CONFIG_PATH, "r", encoding="utf-8") as f:
                    loaded = json.load(f)
                self._data.update(loaded)
                logger.debug("Config loaded from %s", CONFIG_PATH)
            except Exception as e:
                logger.warning("Failed to load config, using defaults: %s", e)
        else:
            self.save()

    def save(self) -> None:
        try:
            CONFIG_DIR.mkdir(parents=True, exist_ok=True)
            with open(CONFIG_PATH, "w", encoding="utf-8") as f:
                json.dump(self._data, f, indent=2, ensure_ascii=False)
        except Exception as e:
            logger.error("Failed to save config: %s", e)

    def get(self, key: str) -> Any:
        return self._data.get(key, DEFAULTS.get(key))

    def set(self, key: str, value: Any) -> None:
        self._data[key] = value
        self.save()

    def has_api_key(self) -> bool:
        return bool(self._data.get("api_key", "").strip())
