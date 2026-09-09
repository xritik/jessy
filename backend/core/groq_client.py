"""
Groq API client wrapper for JESSY.

Handles:
    - Multiple API keys (ideally from separate Groq orgs), selected via
      proactive round-robin so all available per-org rate limits are
      used in parallel instead of hammering a single key until it 429s.
    - Per (key, model) sliding-window RPM tracking: a key that is about
      to breach its per-minute limit for a given model is skipped
      *before* a request is sent, avoiding a wasted failed round-trip.
    - Reactive 429 handling as a safety net: if a request is rate
      limited anyway, that (key, model) pair is put into cooldown and
      the request is retried on the next available key.
    - A "main" model (higher quality, used for final answers) and a
      "tool" model (smaller/faster, used for tool-routing decisions),
      both configurable via environment variables.
    - A Whisper transcription model (audio -> text), also sharing the
      same key pool but tracked against its own, lower RPM budget
      (Groq's free-tier Whisper limit is 20 RPM, well below the
      default chat RPM limit).
"""

from __future__ import annotations

import logging
import os
import threading
import time
from collections import deque
from typing import Any, Optional

from groq import Groq, RateLimitError, APIStatusError

logger = logging.getLogger("jessy.groq_client")

DEFAULT_RPM_LIMIT = 30            # Groq free-tier default, per model per org
DEFAULT_AUDIO_RPM_LIMIT = 20      # Groq free-tier Whisper endpoint limit
DEFAULT_COOLDOWN_SECONDS = 3600.0  # fallback cooldown if Retry-After is absent
WINDOW_SECONDS = 60.0


def _mask_key(key: str) -> str:
    if len(key) <= 12:
        return "****"
    return f"{key[:8]}...{key[-4:]}"


class _KeyState:
    """Tracks rate-limit bookkeeping and the Groq client for a single API key."""

    def __init__(self, api_key: str) -> None:
        self.api_key = api_key
        self.masked = _mask_key(api_key)
        self.client = Groq(api_key=api_key)
        # call_times[model] -> deque of monotonic timestamps within the window
        self.call_times: dict[str, deque[float]] = {}
        # cooldown_until[model] -> monotonic time after which this key is
        # usable again for that model (absent/0.0 means not in cooldown)
        self.cooldown_until: dict[str, float] = {}
        self.lock = threading.Lock()

    def _prune(self, model: str, now: float) -> deque[float]:
        dq = self.call_times.setdefault(model, deque())
        while dq and now - dq[0] > WINDOW_SECONDS:
            dq.popleft()
        return dq

    def is_available(self, model: str, rpm_limit: int, now: float) -> bool:
        with self.lock:
            cooldown = self.cooldown_until.get(model, 0.0)
            if cooldown > now:
                return False
            dq = self._prune(model, now)
            return len(dq) < rpm_limit

    def record_call(self, model: str, now: float) -> None:
        with self.lock:
            dq = self.call_times.setdefault(model, deque())
            dq.append(now)

    def set_cooldown(self, model: str, seconds: float) -> None:
        with self.lock:
            self.cooldown_until[model] = time.monotonic() + seconds

    def cooldown_remaining(self, model: str, now: float) -> float:
        with self.lock:
            return max(0.0, self.cooldown_until.get(model, 0.0) - now)


class GroqClient:
    """
    Wraps one or more Groq API keys (ideally from separate orgs) behind
    a single interface, with proactive round-robin key selection and
    per-(key, model) rate-limit tracking.
    """

    def __init__(
        self,
        api_keys: Optional[list[str]] = None,
        model: Optional[str] = None,
        tool_model: Optional[str] = None,
        whisper_model: Optional[str] = None,
        tts_model: Optional[str] = None,          # <-- new
        rpm_limit: Optional[int] = None,
        audio_rpm_limit: Optional[int] = None,
        tts_rpm_limit: Optional[int] = None,       # <-- new
    ) -> None:
        keys = api_keys or self._load_keys_from_env()
        if not keys:
            raise RuntimeError(
                "No Groq API keys configured. Set GROQ_API_KEY or "
                "GROQ_API_KEY_1..N in the environment."
            )

        self.model = model or os.getenv("GROQ_MODEL", "openai/gpt-oss-120b")
        self.tool_model = tool_model or os.getenv("GROQ_TOOL_MODEL", "openai/gpt-oss-20b")
        self.whisper_model = whisper_model or os.getenv("GROQ_WHISPER_MODEL", "whisper-large-v3-turbo")
        self.rpm_limit = rpm_limit or int(os.getenv("GROQ_RPM_LIMIT", str(DEFAULT_RPM_LIMIT)))
        self.audio_rpm_limit = audio_rpm_limit or int(
            os.getenv("GROQ_AUDIO_RPM_LIMIT", str(DEFAULT_AUDIO_RPM_LIMIT))
        )
        self.tts_model = tts_model or os.getenv("GROQ_TTS_MODEL", "canopylabs/orpheus-v1-english")
        self.tts_rpm_limit = tts_rpm_limit or int(
            os.getenv("GROQ_TTS_RPM_LIMIT", str(DEFAULT_AUDIO_RPM_LIMIT))
        )


        self._keys = [_KeyState(k) for k in keys]
        self._num_keys = len(self._keys)
        self._rr_index = 0  # round-robin pointer, shared across all requests
        self._rr_lock = threading.Lock()

        logger.info(
            "GroqClient initialized with model=%s, tool_model=%s, whisper_model=%s, "
            "%s key(s) available (rpm_limit=%s, audio_rpm_limit=%s per key per model)",
            self.model,
            self.tool_model,
            self.whisper_model,
            self._num_keys,
            self.rpm_limit,
            self.audio_rpm_limit,
        )

    @staticmethod
    def _load_keys_from_env() -> list[str]:
        keys: list[str] = []
        single = os.getenv("GROQ_API_KEY")
        if single:
            keys.append(single)
        i = 1
        while True:
            k = os.getenv(f"GROQ_API_KEY_{i}")
            if not k:
                break
            keys.append(k)
            i += 1
        seen: set[str] = set()
        unique: list[str] = []
        for k in keys:
            if k not in seen:
                seen.add(k)
                unique.append(k)
        return unique

    def _next_round_robin_start(self) -> int:
        with self._rr_lock:
            start = self._rr_index
            self._rr_index = (self._rr_index + 1) % self._num_keys
            return start

    def _pick_key_order(self, model: str, rpm_limit: Optional[int] = None) -> list[int]:
        """
        Order key indices to try: start from the next round-robin slot
        (spreading load evenly), prioritize keys with proactive RPM
        headroom for this model, then fall back to all remaining keys
        in rotation order even if they look unavailable, so a request
        is still attempted rather than failing before hitting the
        network at all.
        """
        limit = rpm_limit or self.rpm_limit
        now = time.monotonic()
        start = self._next_round_robin_start()
        ordered = [(start + i) % self._num_keys for i in range(self._num_keys)]

        available = [i for i in ordered if self._keys[i].is_available(model, limit, now)]
        unavailable = [i for i in ordered if i not in available]
        return available + unavailable

    def chat(
        self,
        messages: list[dict[str, Any]],
        tools: Optional[list[dict[str, Any]]] = None,
        tool_choice: Optional[str] = None,
        model: Optional[str] = None,
        **kwargs: Any,
    ) -> Any:
        """
        Send a chat completion request, proactively selecting a key that
        has RPM headroom for the requested model, and falling back to
        reactive 429 handling (cooldown + retry on the next key) if the
        proactive check turns out to be wrong.
        """
        use_model = model or self.model
        key_order = self._pick_key_order(use_model)

        last_error: Optional[Exception] = None

        for position, key_index in enumerate(key_order):
            key_state = self._keys[key_index]
            now = time.monotonic()
            is_last_option = position == len(key_order) - 1

            if not key_state.is_available(use_model, self.rpm_limit, now) and not is_last_option:
                # Still cooling down or already at its RPM budget for this
                # model; skip without spending a network round-trip.
                continue

            logger.info(
                "Using Groq key #%s (%s) for this request [model=%s]",
                key_index + 1,
                key_state.masked,
                use_model,
            )

            request_kwargs: dict[str, Any] = {
                "model": use_model,
                "messages": messages,
                **kwargs,
            }
            if tools:
                request_kwargs["tools"] = tools
            if tool_choice:
                request_kwargs["tool_choice"] = tool_choice

            try:
                response = key_state.client.chat.completions.create(**request_kwargs)
                key_state.record_call(use_model, time.monotonic())
                return response

            except RateLimitError as exc:
                last_error = exc
                cooldown_seconds = self._extract_retry_after(exc) or DEFAULT_COOLDOWN_SECONDS
                key_state.set_cooldown(use_model, cooldown_seconds)
                logger.warning(
                    "Groq key #%s (%s) hit rate limit; cooling down for %ss",
                    key_index + 1,
                    key_state.masked,
                    int(cooldown_seconds),
                )
                if not is_last_option:
                    next_key_index = key_order[position + 1]
                    logger.info(
                        "Switching from Groq key #%s to key #%s",
                        key_index + 1,
                        next_key_index + 1,
                    )
                continue

            except APIStatusError:
                # Non-rate-limit API error: don't rotate keys for this,
                # just raise it upward as-is.
                raise

        raise RuntimeError(
            "All Groq API keys have hit their rate limit. Please try again later."
        ) from last_error

    def transcribe(
        self,
        audio_bytes: bytes,
        filename: str = "input.wav",
        model: Optional[str] = None,
        prompt: Optional[str] = None,
        language: Optional[str] = None,
        response_format: str = "json",
        temperature: float = 0.0,
        **kwargs: Any,
    ) -> Any:
        """
        Send an audio transcription request to Groq's Whisper endpoint,
        using the same proactive key rotation + reactive 429 handling
        as `chat()`, but tracked against the (lower) audio RPM budget.
        """
        use_model = model or self.whisper_model
        key_order = self._pick_key_order(use_model, rpm_limit=self.audio_rpm_limit)

        last_error: Optional[Exception] = None

        for position, key_index in enumerate(key_order):
            key_state = self._keys[key_index]
            now = time.monotonic()
            is_last_option = position == len(key_order) - 1

            if not key_state.is_available(use_model, self.audio_rpm_limit, now) and not is_last_option:
                continue

            logger.info(
                "Using Groq key #%s (%s) for transcription [model=%s]",
                key_index + 1,
                key_state.masked,
                use_model,
            )

            request_kwargs: dict[str, Any] = {
                "model": use_model,
                "file": (filename, audio_bytes),
                "response_format": response_format,
                "temperature": temperature,
                **kwargs,
            }
            if prompt:
                request_kwargs["prompt"] = prompt
            if language:
                request_kwargs["language"] = language

            try:
                response = key_state.client.audio.transcriptions.create(**request_kwargs)
                key_state.record_call(use_model, time.monotonic())
                return response

            except RateLimitError as exc:
                last_error = exc
                cooldown_seconds = self._extract_retry_after(exc) or DEFAULT_COOLDOWN_SECONDS
                key_state.set_cooldown(use_model, cooldown_seconds)
                logger.warning(
                    "Groq key #%s (%s) hit rate limit on transcription; cooling down for %ss",
                    key_index + 1,
                    key_state.masked,
                    int(cooldown_seconds),
                )
                continue

            except APIStatusError:
                raise

        raise RuntimeError(
            "All Groq API keys have hit their rate limit for transcription. Please try again later."
        ) from last_error


    def speak(
        self,
        text: str,
        voice: str = "austin",
        model: Optional[str] = None,
        response_format: str = "wav",
        **kwargs: Any,
    ) -> bytes:
        """
        Convert text to speech via Groq's Orpheus TTS endpoint, using
        the same proactive key rotation + reactive 429 handling as
        chat() and transcribe(). Returns raw audio bytes.
        """
        use_model = model or self.tts_model
        key_order = self._pick_key_order(use_model, rpm_limit=self.tts_rpm_limit)

        last_error: Optional[Exception] = None

        for position, key_index in enumerate(key_order):
            key_state = self._keys[key_index]
            now = time.monotonic()
            is_last_option = position == len(key_order) - 1

            if not key_state.is_available(use_model, self.tts_rpm_limit, now) and not is_last_option:
                continue

            logger.info(
                "Using Groq key #%s (%s) for TTS [model=%s]",
                key_index + 1,
                key_state.masked,
                use_model,
            )

            request_kwargs: dict[str, Any] = {
                "model": use_model,
                "voice": voice,
                "input": text,
                "response_format": response_format,
                **kwargs,
            }

            try:
                response = key_state.client.audio.speech.create(**request_kwargs)
                key_state.record_call(use_model, time.monotonic())
                return response.read()

            except RateLimitError as exc:
                last_error = exc
                cooldown_seconds = self._extract_retry_after(exc) or DEFAULT_COOLDOWN_SECONDS
                key_state.set_cooldown(use_model, cooldown_seconds)
                logger.warning(
                    "Groq key #%s (%s) hit rate limit on TTS; cooling down for %ss",
                    key_index + 1,
                    key_state.masked,
                    int(cooldown_seconds),
                )
                continue

            except APIStatusError:
                raise

        raise RuntimeError(
            "All Groq API keys have hit their rate limit for TTS. Please try again later."
        ) from last_error


    @staticmethod
    def _extract_retry_after(exc: RateLimitError) -> Optional[float]:
        """Pull the Retry-After value (seconds) from a 429 response, if present."""
        try:
            headers = getattr(exc.response, "headers", None) or {}
            retry_after = headers.get("retry-after")
            if retry_after is not None:
                return float(retry_after)
        except Exception:
            logger.debug("Could not parse Retry-After header from 429 response", exc_info=True)
        return None

# --- Singleton accessor -----------------------------------------------

_client_instance: Optional["GroqClient"] = None
_client_lock = threading.Lock()


def get_groq_client() -> "GroqClient":
    """
    Return a process-wide singleton GroqClient instance, creating it on
    first call using configuration from environment variables.
    """
    global _client_instance
    if _client_instance is None:
        with _client_lock:
            if _client_instance is None:
                _client_instance = GroqClient()
    return _client_instance
