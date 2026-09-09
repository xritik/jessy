"""
Global Action History — Phase 8.

A lightweight, process-wide, in-memory log of every capability call
JESSY has attempted, across all sessions. This backs the GUI's Action
Feed (Phase 11) and the GET /actions endpoint wired up in main.py.

Deliberately separate from agent/context.py's WorkingContext:
    - WorkingContext is per-session and only tracks "current state".
    - This module is global and tracks "everything that happened",
      capped at a fixed size, purely for display/debugging/audit
      purposes.

Not persisted across restarts. If durable history is ever needed,
swap the deque below for a real store (e.g. SQLite) without changing
the public functions (record / get_recent / clear).
"""

from __future__ import annotations

import threading
from collections import deque
from datetime import datetime, timezone
from typing import Any, Optional

from core.result import CapabilityResult

_MAX_ENTRIES = 200

_lock = threading.Lock()
_entries: deque[dict[str, Any]] = deque(maxlen=_MAX_ENTRIES)


def record(
    action: str,
    arguments: dict[str, Any],
    result: Optional[CapabilityResult],
    session_id: str = "default",
    status_override: Optional[str] = None,
) -> None:
    """
    Record one action attempt.

    status_override lets the Agent explicitly mark an entry as
    "proposed" (confirmation required, not yet executed) or
    "cancelled" (user declined a confirmation) rather than inferring
    status purely from result.success. When result is None (e.g. a
    cancelled confirmation, where nothing was ever executed),
    status_override should always be supplied.
    """
    if status_override:
        status = status_override
    elif result is None:
        status = "proposed"
    elif result.success:
        status = "executed"
    else:
        status = "failed"

    if result is not None:
        outcome: Any = result.data if result.success else result.error
    else:
        outcome = None

    with _lock:
        _entries.append(
            {
                "action": action,
                "arguments": arguments,
                "status": status,
                "result": outcome,
                "session_id": session_id,
                "timestamp": datetime.now(timezone.utc).isoformat(),
            }
        )


def get_recent(limit: int = 10) -> list[dict[str, Any]]:
    """Return up to `limit` most recent actions, newest first."""
    with _lock:
        items = list(_entries)
    return list(reversed(items))[:limit]


def clear() -> None:
    """Mainly useful for tests."""
    with _lock:
        _entries.clear()
