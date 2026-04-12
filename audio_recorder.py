"""PyAudio wrapper. Records mic to a temp WAV file with per-chunk RMS metering."""

import pyaudio
import wave
import tempfile
import threading
import struct
import math
import time
import logging
import os
from pathlib import Path
from typing import Callable, Optional

logger = logging.getLogger(__name__)

SAMPLE_RATE = 16000
CHANNELS = 1
SAMPLE_FORMAT = pyaudio.paInt16
SAMPLE_WIDTH = 2  # bytes for paInt16
CHUNK_SIZE = 1024  # ~64ms at 16kHz


class AudioRecorder:
    def __init__(
        self,
        tmp_dir: Path,
        device_index: Optional[int] = None,
        min_duration: float = 0.5,
        silence_threshold: int = 150,
        level_callback: Optional[Callable[[float], None]] = None,
        chunk_callback: Optional[Callable[[bytes], None]] = None,
    ) -> None:
        self._tmp_dir = tmp_dir
        self._device_index = device_index
        self._min_duration = min_duration
        self._silence_threshold = silence_threshold
        self._level_callback = level_callback
        self._chunk_callback = chunk_callback

        self._pa: Optional[pyaudio.PyAudio] = None
        self._stream = None
        self._wav_file = None
        self._tmp_path: Optional[str] = None
        self._thread: Optional[threading.Thread] = None
        self._stop_event = threading.Event()
        self._start_time: float = 0.0
        self._rms_sum: float = 0.0
        self._chunk_count: int = 0
        self._lock = threading.Lock()

    def list_devices(self) -> list[dict]:
        """Returns list of available input devices."""
        pa = pyaudio.PyAudio()
        devices = []
        try:
            for i in range(pa.get_device_count()):
                info = pa.get_device_info_by_index(i)
                if info.get("maxInputChannels", 0) > 0:
                    name = self._fix_device_name(info["name"])
                    devices.append({
                        "index": i,
                        "name": name,
                        "max_input_channels": info["maxInputChannels"],
                    })
        finally:
            pa.terminate()
        return devices

    @staticmethod
    def _fix_device_name(name: str) -> str:
        """Fix garbled device names from PyAudio on Windows.

        PyAudio/PortAudio returns UTF-8 bytes, but Python decodes them
        using the system codepage (e.g. cp1251). Re-encode back to the
        original bytes, then decode as UTF-8.
        """
        import locale
        codepage = locale.getpreferredencoding(False)  # e.g. 'cp1251'
        try:
            raw_bytes = name.encode(codepage)
            return raw_bytes.decode("utf-8")
        except (UnicodeDecodeError, UnicodeEncodeError):
            return name

    def start(self) -> None:
        """Open stream and start recording in background thread."""
        self._stop_event.clear()
        self._rms_sum = 0.0
        self._chunk_count = 0

        self._pa = pyaudio.PyAudio()
        tmp_fd, self._tmp_path = tempfile.mkstemp(suffix=".wav", dir=self._tmp_dir)
        os.close(tmp_fd)

        self._wav_file = wave.open(self._tmp_path, "wb")
        self._wav_file.setnchannels(CHANNELS)
        self._wav_file.setsampwidth(SAMPLE_WIDTH)
        self._wav_file.setframerate(SAMPLE_RATE)

        self._stream = self._pa.open(
            format=SAMPLE_FORMAT,
            channels=CHANNELS,
            rate=SAMPLE_RATE,
            input=True,
            input_device_index=self._device_index,
            frames_per_buffer=CHUNK_SIZE,
        )

        self._start_time = time.time()
        self._thread = threading.Thread(target=self._record_loop, daemon=True)
        self._thread.start()
        logger.info("Recording started, device_index=%s", self._device_index)

    def stop(self) -> Optional[str]:
        """
        Stop recording. Returns path to WAV file, or None if too short/silent.
        Caller must delete the file after use.
        """
        self._stop_event.set()
        if self._thread:
            self._thread.join(timeout=3.0)

        duration = time.time() - self._start_time

        if self._stream:
            try:
                self._stream.stop_stream()
                self._stream.close()
            except Exception:
                pass
            self._stream = None

        if self._wav_file:
            try:
                self._wav_file.close()
            except Exception:
                pass
            self._wav_file = None

        if self._pa:
            try:
                self._pa.terminate()
            except Exception:
                pass
            self._pa = None

        path = self._tmp_path
        self._tmp_path = None

        if duration < self._min_duration:
            logger.info("Recording too short (%.2fs), discarding", duration)
            self._delete_file(path)
            return None

        if self._is_silent():
            logger.info("Recording is silent, discarding")
            self._delete_file(path)
            return None

        logger.info("Recording stopped, %.2fs, path=%s", duration, path)
        return path

    def _record_loop(self) -> None:
        while not self._stop_event.is_set():
            try:
                chunk = self._stream.read(CHUNK_SIZE, exception_on_overflow=False)
            except Exception as e:
                logger.error("Stream read error: %s", e)
                break

            self._wav_file.writeframes(chunk)

            if self._chunk_callback:
                try:
                    self._chunk_callback(chunk)
                except Exception:
                    pass

            rms = self._compute_rms(chunk)
            with self._lock:
                self._rms_sum += rms ** 2
                self._chunk_count += 1

            if self._level_callback:
                normalised = min(rms / 3000.0, 1.0)
                try:
                    self._level_callback(normalised)
                except Exception:
                    pass

    def _compute_rms(self, chunk: bytes) -> float:
        """Compute RMS from raw int16 bytes."""
        count = len(chunk) // 2
        if count == 0:
            return 0.0
        fmt = f"<{count}h"
        samples = struct.unpack(fmt, chunk)
        rms = math.sqrt(sum(s * s for s in samples) / count)
        return rms

    def _is_silent(self) -> bool:
        with self._lock:
            if self._chunk_count == 0:
                return True
            avg_rms = math.sqrt(self._rms_sum / self._chunk_count)
        return avg_rms < self._silence_threshold

    def _delete_file(self, path: Optional[str]) -> None:
        if path:
            try:
                os.unlink(path)
            except Exception:
                pass

    def cleanup_old_tmp_files(self) -> None:
        """Delete WAV files older than 1 hour from tmp dir."""
        cutoff = time.time() - 3600
        for f in self._tmp_dir.glob("*.wav"):
            try:
                if f.stat().st_mtime < cutoff:
                    f.unlink()
                    logger.debug("Deleted old temp file: %s", f)
            except Exception:
                pass
