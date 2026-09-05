"""
Thin wrapper around the Groq SDK.

Keeps model name, API key, and call parameters centralized and
environment-driven — never hardcoded. This is the ONLY place in the
codebase that talks to Groq directly.
"""

from __future__ import annotations

import logging
import os
from typing import Any, Optional

from groq import Groq

logger = logging.getLogger("jessy.groq_client")


class GroqClient:
    """Wraps the Groq SDK for chat completions with tool calling."""

    def __init__(self) -> None:
        api_key = os.getenv("GROQ_API_KEY")
        if not api_key or api_key == "your_key_here":
            raise RuntimeError(
                "GROQ_API_KEY is not set. Add a real key to backend/.env"
            )

        self.model = os.getenv("GROQ_MODEL", "openai/gpt-oss-120b")
        self._client = Groq(api_key=api_key)
        logger.info("GroqClient initialized with model=%s", self.model)

    def chat(
        self,
        messages: list[dict[str, Any]],
        tools: Optional[list[dict[str, Any]]] = None,
        tool_choice: str = "auto",
        temperature: float = 0.3,
    ) -> Any:
        """
        Send a chat completion request to Groq.

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

        try:
            return self._client.chat.completions.create(**kwargs)
        except Exception as exc:
            logger.exception("Groq API call failed")
            raise RuntimeError(f"Groq API call failed: {exc}") from exc


# Lazily created singleton — instantiated on first use so importing this
# module never fails just because .env isn't loaded yet at import time.
_groq_client_instance: Optional[GroqClient] = None


def get_groq_client() -> GroqClient:
    """Return the shared GroqClient instance, creating it if needed."""
    global _groq_client_instance
    if _groq_client_instance is None:
        _groq_client_instance = GroqClient()
    return _groq_client_instance
