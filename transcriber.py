"""AssemblyAI batch and real-time transcription."""

import assemblyai as aai
import logging
import threading
import time
from dataclasses import dataclass
from typing import Callable, Optional

logger = logging.getLogger(__name__)

MAX_RETRIES = 3
RETRY_BACKOFF = [2, 5, 10]  # seconds between retries

# Errors worth retrying (network / transient)
_RETRYABLE_KEYWORDS = [
    "getaddrinfo",
    "connection",
    "timeout",
    "temporary",
    "unavailable",
    "reset",
    "eof",
    "broken pipe",
    "ssl",
    "network",
    "socket",
    "resolve",
    "dns",
]


def _is_retryable(error: Exception) -> bool:
    msg = str(error).lower()
    return any(kw in msg for kw in _RETRYABLE_KEYWORDS)


@dataclass
class TranscriptResult:
    text: Optional[str]
    error: Optional[str]
    language_code: Optional[str] = None


class Transcriber:
    TIMEOUT_SECONDS = 90

    def __init__(self, api_key: str) -> None:
        self._api_key = api_key
        self._configure()

    def _configure(self) -> None:
        aai.settings.api_key = self._api_key

    def update_api_key(self, api_key: str) -> None:
        self._api_key = api_key
        self._configure()

    def transcribe(
        self,
        wav_path: str,
        progress_callback: Optional[Callable[[str], None]] = None,
    ) -> TranscriptResult:
        result_holder: list[TranscriptResult] = []
        exception_holder: list[Exception] = []

        def _worker():
            last_error: Optional[Exception] = None

            for attempt in range(1, MAX_RETRIES + 1):
                try:
                    if progress_callback:
                        if attempt > 1:
                            progress_callback(f"Retry {attempt}/{MAX_RETRIES}...")
                        else:
                            progress_callback("Uploading...")

                    config = aai.TranscriptionConfig(
                        language_detection=True,
                    )
                    transcriber = aai.Transcriber()
                    transcript = transcriber.transcribe(wav_path, config=config)

                    if transcript.status == aai.TranscriptStatus.error:
                        result_holder.append(TranscriptResult(
                            text=None,
                            error=transcript.error or "API error",
                        ))
                    else:
                        text = (transcript.text or "").strip()
                        result_holder.append(TranscriptResult(
                            text=text if text else None,
                            error=None if text else "No speech detected",
                            language_code=getattr(transcript, "language_code", None),
                        ))
                    return  # success — exit retry loop

                except Exception as e:
                    last_error = e
                    if attempt < MAX_RETRIES and _is_retryable(e):
                        wait = RETRY_BACKOFF[attempt - 1]
                        logger.warning(
                            "Transcription attempt %d failed (%s), retrying in %ds...",
                            attempt, e, wait,
                        )
                        if progress_callback:
                            progress_callback(f"Network error, retrying in {wait}s...")
                        time.sleep(wait)
                    else:
                        break

            exception_holder.append(last_error)

        t = threading.Thread(target=_worker, daemon=True)
        t.start()
        t.join(timeout=self.TIMEOUT_SECONDS)

        if t.is_alive():
            logger.error("Transcription timed out after %ss", self.TIMEOUT_SECONDS)
            return TranscriptResult(text=None, error="Timeout (90s)")

        if exception_holder:
            logger.error("Transcription exception: %s", exception_holder[0])
            return TranscriptResult(text=None, error=str(exception_holder[0]))

        if result_holder:
            return result_holder[0]

        return TranscriptResult(text=None, error="Unknown error")

    def validate_api_key(self, api_key: str) -> tuple[bool, str]:
        try:
            old_key = self._api_key
            aai.settings.api_key = api_key
            aai.Transcriber().list_transcripts()
            aai.settings.api_key = old_key
            return True, "Key is valid"
        except aai.AssemblyAIError as e:
            aai.settings.api_key = self._api_key
            msg = str(e).lower()
            if "unauthorized" in msg or "401" in msg or "invalid" in msg:
                return False, "Invalid API key"
            return False, f"Error: {e}"
        except Exception as e:
            aai.settings.api_key = self._api_key
            return False, f"Connection error: {e}"

    def create_realtime_session(
        self,
        on_text: Optional[Callable[[str, bool], None]] = None,
        on_error: Optional[Callable[[str], None]] = None,
        sample_rate: int = 16000,
    ) -> "RealtimeSession":
        return RealtimeSession(
            api_key=self._api_key,
            on_text=on_text,
            on_error=on_error,
            sample_rate=sample_rate,
        )


class RealtimeSession:
    """Wraps AssemblyAI V3 StreamingClient for live transcription."""

    def __init__(
        self,
        api_key: str,
        on_text: Optional[Callable[[str, bool], None]] = None,
        on_error: Optional[Callable[[str], None]] = None,
        sample_rate: int = 16000,
    ) -> None:
        self._api_key = api_key
        self._on_text = on_text
        self._on_error = on_error
        self._sample_rate = sample_rate
        self._client: Optional[StreamingClient] = None
        self._finals: list[str] = []
        self._current_partial: str = ""
        self._lock = threading.Lock()
        self._connected = False

    def start(self) -> None:
        """Connect to AssemblyAI V3 streaming endpoint."""
        from assemblyai.streaming.v3.client import StreamingClient
        from assemblyai.streaming.v3.models import (
            StreamingClientOptions,
            StreamingParameters,
            StreamingEvents,
            SpeechModel,
            Encoding,
        )

        options = StreamingClientOptions(
            api_key=self._api_key,
        )
        self._client = StreamingClient(options)

        # Register event handlers
        self._client.on(StreamingEvents.Begin, self._on_begin)
        self._client.on(StreamingEvents.Turn, self._on_turn)
        self._client.on(StreamingEvents.Error, self._on_stream_error)
        self._client.on(StreamingEvents.Termination, self._on_termination)

        params = StreamingParameters(
            sample_rate=self._sample_rate,
            speech_model=SpeechModel.universal_streaming_multilingual,
            encoding=Encoding.pcm_s16le,
        )
        self._client.connect(params)
        logger.info("V3 streaming session started")

    def send_audio(self, chunk: bytes) -> None:
        """Stream raw PCM audio bytes."""
        if self._client and self._connected:
            try:
                self._client.stream(chunk)
            except Exception as e:
                logger.debug("Streaming send error: %s", e)

    def stop(self) -> str:
        """Disconnect and return accumulated text."""
        if self._client:
            try:
                self._client.disconnect(terminate=True)
            except Exception as e:
                logger.debug("Streaming disconnect error: %s", e)
            self._client = None
        self._connected = False

        with self._lock:
            parts = list(self._finals)
            if self._current_partial:
                parts.append(self._current_partial)
            return " ".join(parts).strip()

    def get_current_text(self) -> str:
        """Get the current accumulated text (finals + partial)."""
        with self._lock:
            parts = list(self._finals)
            if self._current_partial:
                parts.append(self._current_partial)
            return " ".join(parts).strip()

    def _on_begin(self, client, event) -> None:
        self._connected = True
        logger.info("Streaming session opened: %s", event.id)

    def _on_turn(self, client, event) -> None:
        text = event.transcript
        if not text:
            return

        with self._lock:
            if event.end_of_turn:
                self._finals.append(text)
                self._current_partial = ""
                is_final = True
            else:
                self._current_partial = text
                is_final = False

        full_text = self.get_current_text()
        if self._on_text:
            try:
                self._on_text(full_text, is_final)
            except Exception:
                pass

    def _on_stream_error(self, client, error) -> None:
        logger.error("Streaming error: %s", error)
        if self._on_error:
            try:
                self._on_error(str(error))
            except Exception:
                pass

    def _on_termination(self, client, event) -> None:
        self._connected = False
        logger.info("Streaming session terminated")
