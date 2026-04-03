"""AssemblyAI batch transcription with auto language detection and retry."""

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
                        speech_models=[
                            aai.SpeechModel.conformer_2,  # Universal-3 Pro
                            aai.SpeechModel.nano,         # Universal-2 fallback
                        ],
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
