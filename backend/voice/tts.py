"""
Text-to-Speech via Piper — fully local, offline neural TTS.

Mirrors voice/whisper.py's shape on purpose: a small wrapper that
never raises. Callers get back a SpeechResult with success/error
instead of an exception, so main.py can degrade gracefully (text-only
response, no crash) exactly like it already does for transcription
failures and chat rate limits.

Unlike the Groq-based version, this has:
    - no API key or network dependency
    - no rate limits
    - no per-org terms acceptance
    - no per-request cost
The voice model is loaded once (lazily, on first use) and reused for
every subsequent call, since loading the ONNX model is the expensive
part — synthesis itself is fast once it's loaded.
"""

from __future__ import annotations

import io
import logging
import os
import threading
import wave
from dataclasses import dataclass
from typing import Optional

logger = logging.getLogger("jessy.voice.tts")

DEFAULT_VOICE_MODEL_PATH = os.getenv(
    "PIPER_VOICE_MODEL_PATH", "models/piper/en_US-lessac-medium.onnx"
)

# Piper has no hard input-length cap like Groq's Orpheus does, but an
# absurdly long string is almost certainly a bug upstream (e.g. the
# whole chat history leaking into a spoken reply), so guard against
# pathological input freezing a request for tens of seconds.
_MAX_TTS_CHARS = 4000

_voice_cache: dict[str, object] = {}
_voice_lock = threading.Lock()


@dataclass
class SpeechResult:
    success: bool
    audio_bytes: Optional[bytes] = None
    content_type: str = "audio/wav"
    error: Optional[str] = None


def _load_voice(model_path: str):
    """
    Load (or fetch from cache) a PiperVoice for the given model path.
    Loading is the slow part (~1-2s), so every voice used is cached
    for the lifetime of the process after its first request.
    """
    if model_path in _voice_cache:
        return _voice_cache[model_path]

    with _voice_lock:
        if model_path in _voice_cache:  # re-check after acquiring lock
            return _voice_cache[model_path]

        from piper import PiperVoice  # imported here so a missing

        if not os.path.isfile(model_path):
            raise FileNotFoundError(
                f"Piper voice model not found at '{model_path}'. "
                f"Run: python -m piper.download_voices en_US-lessac-medium "
                f"and set PIPER_VOICE_MODEL_PATH, or place the .onnx file there."
            )

        logger.info("Loading Piper voice model from %s (first use, one-time cost)", model_path)
        voice = PiperVoice.load(model_path)
        _voice_cache[model_path] = voice
        return voice


def synthesize_speech(
    text: str,
    voice: Optional[str] = None,
    response_format: str = "wav",  # kept for interface compatibility; Piper always outputs wav
    model: Optional[str] = None,   # optional override of the voice model path
) -> SpeechResult:
    """
    Convert `text` into speech audio bytes using a local Piper voice.

    Never raises. Any failure (empty text, missing model file, runtime
    error during synthesis) comes back as SpeechResult(success=False,
    error=...), so callers can fall back to a text-only response
    instead of a 500.

    `voice`/`model` may both be used to point at a specific voice's
    model path; if neither is given, PIPER_VOICE_MODEL_PATH (or its
    default) is used. Multiple voices can be swapped between calls
    freely — each is cached independently after first load.
    """
    if not text or not text.strip():
        return SpeechResult(success=False, error="No text provided to synthesize.")

    trimmed = text if len(text) <= _MAX_TTS_CHARS else text[: _MAX_TTS_CHARS - 1] + "…"
    model_path = model or voice or DEFAULT_VOICE_MODEL_PATH

    try:
        piper_voice = _load_voice(model_path)

        buffer = io.BytesIO()
        with wave.open(buffer, "wb") as wav_file:
            piper_voice.synthesize_wav(trimmed, wav_file)
        audio_bytes = buffer.getvalue()

    except FileNotFoundError as exc:
        logger.error("Piper voice model missing: %s", exc)
        return SpeechResult(success=False, error=str(exc))
    except Exception as exc:  # noqa: BLE001 — surfaced via SpeechResult.error
        logger.exception("Piper TTS synthesis failed")
        return SpeechResult(success=False, error=str(exc))

    return SpeechResult(success=True, audio_bytes=audio_bytes, content_type="audio/wav")
