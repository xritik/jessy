"""
Voice State — Phase 9.4

Tracks a simple per-session voice state machine so that later (Phase
10's WebSocket layer) the GUI can show accurate real-time status
("JESSY is listening...", "JESSY is thinking...", "JESSY is
speaking...") instead of guessing from HTTP request/response timing.

Phase 9's /voice endpoint is still a single request/response round
trip (a full audio clip uploaded, a full response returned) — this
module does not drive live audio capture. What it tracks is *logical*
pipeline state, updated by agent/agent.py and main.py at the right
points during that round trip:

    idle -> listening (STT running) -> thinking (Agent loop running)
         -> speaking (TTS running) -> idle

Right now the only way to observe a transition is the debug endpoint
below or the server log line each transition emits, since a single
HTTP request completes before you could poll it mid-flight. That's
expected for this phase — Phase 10's WebSocket will push these same
transitions to the GUI live, as they happen, instead of after the
fact.

Process model note: identical caveat to agent/context.py and
agent/agent.py's _pending_confirmations — a plain in-memory dict at
module scope, only reliable with a single uvicorn worker and no
--reload.
"""

from __future__ import annotations

import logging
import threading
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Any

logger = logging.getLogger("jessy.voice.state")


class VoiceState(str, Enum):
    """
    idle      - nothing happening for this session right now
    listening - audio has been received and is being transcribed (STT)
    thinking  - the transcript/message is being run through the Agent loop
    speaking  - the final response is being synthesized to audio (TTS)
    """

    IDLE = "idle"
    LISTENING = "listening"
    THINKING = "thinking"
    SPEAKING = "speaking"


@dataclass
class VoiceStatus:
    """Current voice state for a single session, plus when it last changed."""

    state: VoiceState = VoiceState.IDLE
    updated_at: str = field(
        default_factory=lambda: datetime.now(timezone.utc).isoformat()
    )

    def to_dict(self) -> dict[str, Any]:
        return {"state": self.state.value, "updated_at": self.updated_at}


_states: dict[str, VoiceStatus] = {}
_lock = threading.Lock()


def set_state(session_id: str, state: VoiceState) -> None:
    """Set (or create) the voice state for a session."""
    with _lock:
        previous = _states.get(session_id)
        _states[session_id] = VoiceStatus(
            state=state, updated_at=datetime.now(timezone.utc).isoformat()
        )
    if previous is None or previous.state != state:
        logger.debug(
            "Voice state for session %s: %s -> %s",
            session_id, previous.state.value if previous else "unknown", state.value,
        )


def get_state(session_id: str) -> VoiceStatus:
    """Return the current voice state for a session, defaulting to IDLE
    if the session hasn't been seen yet (never raises a KeyError)."""
    with _lock:
        return _states.get(session_id, VoiceStatus())


def reset_state(session_id: str) -> None:
    """Explicitly return a session to idle (e.g. after a full turn
    completes, or on error). Equivalent to set_state(session_id,
    VoiceState.IDLE) but named for clarity at call sites."""
    set_state(session_id, VoiceState.IDLE)


def clear_state(session_id: str) -> None:
    """Drop tracking for a session entirely (e.g. on 'new conversation')."""
    with _lock:
        _states.pop(session_id, None)
