"""Persistent history of recent transcriptions stored as JSON."""

import json
import logging
from dataclasses import dataclass, asdict
from datetime import datetime
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)

MAX_ENTRIES = 50


@dataclass
class HistoryEntry:
    text: str
    timestamp: str  # ISO 8601
    language: Optional[str] = None

    def display_time(self) -> str:
        try:
            dt = datetime.fromisoformat(self.timestamp)
            return dt.strftime("%Y-%m-%d %H:%M")
        except Exception:
            return self.timestamp


class HistoryManager:
    def __init__(self, path: Path) -> None:
        self._path = path
        self._entries: list[HistoryEntry] = []
        self._load()

    def _load(self) -> None:
        if not self._path.exists():
            return
        try:
            with open(self._path, "r", encoding="utf-8") as f:
                raw = json.load(f)
            self._entries = [HistoryEntry(**e) for e in raw]
        except Exception as e:
            logger.warning("Failed to load history: %s", e)

    def _save(self) -> None:
        try:
            self._path.parent.mkdir(parents=True, exist_ok=True)
            with open(self._path, "w", encoding="utf-8") as f:
                json.dump([asdict(e) for e in self._entries], f, ensure_ascii=False, indent=2)
        except Exception as e:
            logger.error("Failed to save history: %s", e)

    def add(self, text: str, language: Optional[str] = None) -> None:
        entry = HistoryEntry(
            text=text,
            timestamp=datetime.now().isoformat(timespec="seconds"),
            language=language,
        )
        self._entries.insert(0, entry)
        if len(self._entries) > MAX_ENTRIES:
            self._entries = self._entries[:MAX_ENTRIES]
        self._save()

    def get_all(self) -> list[HistoryEntry]:
        return list(self._entries)

    def clear(self) -> None:
        self._entries.clear()
        self._save()
