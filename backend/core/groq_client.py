"""
Thin wrapper around the Groq SDK.

Keeps model name, API keys, and call parameters centralized and
environment-driven — never hardcoded. This is the ONLY place in the
codebase that talks to Groq directly.

Supports multiple API keys (GROQ_API_KEY_1, GROQ_API_KEY_2, ...) so that
when one key hits its free-tier rate limit, calls automatically rotate
to the next available key instead of failing the whole request. Once a
key hits a limit it's put in "cooldown" for a while and skipped until
that cooldown expires, then it becomes eligible again — cycling
1 -> 2 -> ... -> N -> back to 1.
"""

from __future__ import annotations

import logging
import os
import time
from typing import Any, Optional

from groq import Groq

try:
    from groq import RateLimitError
except ImportError:  # pragma: no cover - safety net if SDK renames this
    RateLimitError = None  # type: ignore[assignment]

logger = logging.getLogger("jessy.groq_client")

# How long to keep a rate-limited key in "cooldown" before it's eligible
# again. Groq free-tier limits are commonly per-minute/day; this is a
# conservative default, and we shorten it automatically if the API
# response includes a Retry-After header.
_DEFAULT_COOLDOWN_SECONDS = 60 * 60  # 1 hour


def _load_api_keys() -> list[str]:
    """Collect all configured Groq API keys, in order.

    Supports GROQ_API_KEY_1, GROQ_API_KEY_2, ... (preferred, enables
    rotation) and falls back to a single GROQ_API_KEY for backward
    compatibility with older .env files.
    """
    keys: list[str] = []
    i = 1
    while True:
        key = os.getenv(f"GROQ_API_KEY_{i}")
        if key is None:
            break
        if key and key != "your_key_here":
            keys.append(key)
        i += 1

    if not keys:
        single = os.getenv("GROQ_API_KEY")
        if single and single != "your_key_here":
            keys.append(single)

    return keys


def _is_rate_limit_error(exc: Exception) -> bool:
    """Detect a 429 rate-limit error robustly, even if the SDK's
    exception class changes name between versions."""
    if RateLimitError is not None and isinstance(exc, RateLimitError):
        return True
    status_code = getattr(exc, "status_code", None)
    if status_code == 429:
        return True
    response = getattr(exc, "response", None)
    if response is not None and getattr(response, "status_code", None) == 429:
        return True
    return False


def _extract_retry_after(exc: Exception) -> Optional[float]:
    """Best-effort extraction of a Retry-After hint from the
    exception's HTTP response, if the SDK exposes it."""
    response = getattr(exc, "response", None)
    headers = getattr(response, "headers", None) if response else None
    if not headers:
        return None
    value = headers.get("retry-after") or headers.get("Retry-After")
    try:
        return float(value) if value is not None else None
    except (TypeError, ValueError):
        return None


class GroqClient:
    """Wraps the Groq SDK for chat completions with tool calling.

    Rotates across multiple API keys when one is rate-limited (HTTP
    429), so a single exhausted free-tier key doesn't take the whole
    assistant down. Only 429s trigger rotation — other errors (bad
    request, network issue, etc.) still fail immediately as before.
    """

    def __init__(self) -> None:
        self._keys = _load_api_keys()
        if not self._keys:
            raise RuntimeError(
                "No Groq API key found. Add GROQ_API_KEY_1 (and "
                "optionally _2, _3, ...) to backend/.env"
            )

        self.model = os.getenv("GROQ_MODEL", "openai/gpt-oss-120b")

        # Per-key cooldown-until timestamp (monotonic seconds). 0 means
        # "not in cooldown, available right now".
        self._cooldown_until: dict[int, float] = {i: 0.0 for i in range(len(self._keys))}
        self._current_index = 0
        self._client = Groq(api_key=self._keys[self._current_index])

        logger.info(
            "GroqClient initialized with model=%s, %d key(s) available",
            self.model,
            len(self._keys),
        )

    @staticmethod
    def _mask(key: str) -> str:
        return f"{key[:8]}...{key[-4:]}" if len(key) > 12 else "***"

    def _switch_to(self, index: int) -> None:
        self._current_index = index
        self._client = Groq(api_key=self._keys[index])

    def _mark_rate_limited(self, index: int, retry_after: Optional[float]) -> None:
        cooldown = retry_after if retry_after and retry_after > 0 else _DEFAULT_COOLDOWN_SECONDS
        self._cooldown_until[index] = time.monotonic() + cooldown
        logger.warning(
            "Groq key #%d (%s) hit rate limit; cooling down for %.0fs",
            index + 1,
            self._mask(self._keys[index]),
            cooldown,
        )

    def _next_available_index(self, exclude: set[int]) -> Optional[int]:
        """Next key not currently in cooldown and not already tried this
        call, cycling forward from the current index and wrapping
        around (N -> back to 1). Returns None if none are available."""
        now = time.monotonic()
        n = len(self._keys)
        for step in range(1, n + 1):
            idx = (self._current_index + step) % n
            if idx in exclude:
                continue
            if self._cooldown_until[idx] <= now:
                return idx
        return None

    def chat(
        self,
        messages: list[dict[str, Any]],
        tools: Optional[list[dict[str, Any]]] = None,
        tool_choice: str = "auto",
        temperature: float = 0.3,
    ) -> Any:
        """
        Send a chat completion request to Groq.

        Automatically rotates to the next available (non-rate-limited)
        key if the current one returns a 429, retrying the SAME request
        transparently. Tries each key at most once per call; if every
        key is currently rate-limited, raises one clean error instead
        of a raw stack trace.

        Returns the raw SDK response object. Callers inspect
        `response.choices[0].message` for content and/or tool_calls.
        """
        kwargs: dict[str, Any] = {
            "model": self.model,
            "messages": messages,
            "temperature": temperature,
        }
        if tools:
            kwargs["tools"] = tools
            kwargs["tool_choice"] = tool_choice

        tried: set[int] = set()
        n = len(self._keys)

        for _ in range(n):
            if self._cooldown_until[self._current_index] > time.monotonic():
                next_idx = self._next_available_index(tried)
                if next_idx is None:
                    break
                self._switch_to(next_idx)

            tried.add(self._current_index)

            logger.info(
                "Using Groq key #%d (%s) for this request",
                self._current_index + 1,
                self._mask(self._keys[self._current_index]),
            )

            try:
                return self._client.chat.completions.create(**kwargs)
            except Exception as exc:
                if not _is_rate_limit_error(exc):
                    logger.exception("Groq API call failed")
                    raise RuntimeError(f"Groq API call failed: {exc}") from exc

                self._mark_rate_limited(self._current_index, _extract_retry_after(exc))

                next_idx = self._next_available_index(tried)
                if next_idx is None:
                    break
                logger.info(
                    "Switching from Groq key #%d to key #%d",
                    self._current_index + 1,
                    next_idx + 1,
                )
                self._switch_to(next_idx)

        logger.error("All %d Groq API key(s) are currently rate-limited", n)
        raise RuntimeError(
            "All Groq API keys have hit their rate limit. Please try again later."
        )


# Lazily created singleton — instantiated on first use so importing this
# module never fails just because .env isn't loaded yet at import time.
_groq_client_instance: Optional[GroqClient] = None


def get_groq_client() -> GroqClient:
    """Return the shared GroqClient instance, creating it if needed."""
    global _groq_client_instance
    if _groq_client_instance is None:
        _groq_client_instance = GroqClient()
    return _groq_client_instance
