"""
Whisper speech-to-text module for JESSY.

Uses Groq's hosted Whisper API (whisper-large-v3-turbo by default) to
convert recorded audio into text. This is JESSY's "ears" - the STT
layer described in the voice architecture:

    MICROPHONE -> WHISPER STT -> TEXT -> JESSY AGENT

This module does not touch the microphone directly (that happens in
the browser/GUI, or via an uploaded audio blob through the /voice API
endpoint built in the next step). It only turns raw audio bytes into
transcribed text.
"""

from __future__ import annotations

import logging
import os
import time
from dataclasses import dataclass
from typing import Optional
from dotenv import load_dotenv;
load_dotenv()

from groq import APIStatusError

from core.groq_client import get_groq_client

logger = logging.getLogger("jessy.voice.whisper")

# Formats accepted by Groq's transcription endpoint.
SUPPORTED_EXTENSIONS = {
    ".flac", ".mp3", ".mp4", ".m4a", ".mpeg", ".mpga",
    ".ogg", ".opus", ".wav", ".webm",
}

# Groq free-tier hard limit; dev tier allows up to 100 MB.
MAX_AUDIO_BYTES = 25 * 1024 * 1024


@dataclass
class TranscriptionResult:
    success: bool
    text: str = ""
    language: Optional[str] = None
    duration_seconds: Optional[float] = None
    error: Optional[str] = None


def transcribe_audio(
    audio_bytes: bytes,
    filename: str = "input.wav",
    language: Optional[str] = None,
    prompt: Optional[str] = None,
) -> TranscriptionResult:
    """
    Transcribe raw audio bytes into text using Groq's Whisper API.

    Args:
        audio_bytes: Raw audio file content (wav/mp3/m4a/webm/ogg/etc).
        filename: Original filename, used only to hint the file
            extension/content-type to the API. Does not need to exist
            on disk.
        language: Optional ISO-639-1 language code (e.g. "en") to
            improve accuracy and skip language detection. Leave None
            to let Whisper auto-detect.
        prompt: Optional short text to bias transcription toward
            expected vocabulary (e.g. project or file names JESSY
            commonly hears).

    Returns:
        TranscriptionResult with the transcribed text on success, or
        an error message on failure. Never raises for expected
        failure modes (empty audio, oversized file, API errors) -
        callers should check `.success`.
    """
    if not audio_bytes:
        return TranscriptionResult(success=False, error="No audio data received.")

    if len(audio_bytes) > MAX_AUDIO_BYTES:
        return TranscriptionResult(
            success=False,
            error=(
                f"Audio file is {len(audio_bytes) / (1024 * 1024):.1f} MB, "
                f"which exceeds the {MAX_AUDIO_BYTES // (1024 * 1024)} MB limit."
            ),
        )

    ext = os.path.splitext(filename)[1].lower()
    if ext and ext not in SUPPORTED_EXTENSIONS:
        logger.warning("Unrecognized audio extension '%s'; attempting transcription anyway.", ext)

    start = time.monotonic()
    try:
        client = get_groq_client()
        response = client.transcribe(
            audio_bytes=audio_bytes,
            filename=filename or "input.wav",
            language=language,
            prompt=prompt,
            response_format="json",
            temperature=0.0,
        )
    except APIStatusError as exc:
        logger.error("Groq transcription API error: %s", exc)
        return TranscriptionResult(success=False, error=f"Transcription API error: {exc}")
    except RuntimeError as exc:
        # Raised by GroqClient when every key is exhausted/rate-limited.
        logger.error("Transcription failed: %s", exc)
        return TranscriptionResult(success=False, error=str(exc))
    except Exception as exc:  # noqa: BLE001 - never let a bad audio blob crash the agent loop
        logger.exception("Unexpected error during transcription")
        return TranscriptionResult(success=False, error=f"Unexpected transcription error: {exc}")

    elapsed = time.monotonic() - start
    text = (getattr(response, "text", "") or "").strip()

    if not text:
        logger.info("Transcription returned empty text (%.2fs, %d bytes audio)", elapsed, len(audio_bytes))
        return TranscriptionResult(success=False, error="No speech detected in audio.")

    logger.info("Transcribed %d bytes of audio in %.2fs: %r", len(audio_bytes), elapsed, text[:80])

    return TranscriptionResult(
        success=True,
        text=text,
        language=language,
        duration_seconds=elapsed,
    )
