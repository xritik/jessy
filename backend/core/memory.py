"""
Simple in-memory conversation history, keyed by session_id.
Not persisted across restarts - fine for now, revisit if you want
durable memory later.
"""

from typing import Dict, List

_sessions: Dict[str, List[dict]] = {}
MAX_TURNS = 20  # keep last N messages per session


def get_history(session_id: str) -> List[dict]:
    return _sessions.setdefault(session_id, [])


def append_message(session_id: str, role: str, content: str) -> None:
    history = get_history(session_id)
    history.append({"role": role, "content": content})
    if len(history) > MAX_TURNS:
        del history[: len(history) - MAX_TURNS]
