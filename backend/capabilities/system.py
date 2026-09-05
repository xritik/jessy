"""
System capabilities.

Step 2 includes only a single, trivial, safe capability
(`get_current_time`) to prove the agent loop end-to-end.

Real system-info capabilities (cpu_usage, memory_usage, disk_usage,
battery_status, network_status) are added in Phase 3 using psutil.
"""

from __future__ import annotations

from datetime import datetime, timezone

from core.registry import registry


def get_current_time() -> dict:
    """Return the current date and time in UTC and local form."""
    now_utc = datetime.now(timezone.utc)
    now_local = datetime.now()
    return {
        "utc_iso": now_utc.isoformat(),
        "local": now_local.strftime("%Y-%m-%d %H:%M:%S"),
    }


registry.register(
    name="get_current_time",
    function=get_current_time,
    description=(
        "Get the current date and time, in both UTC and the server's "
        "local time. Use this whenever the user asks what time or date "
        "it is."
    ),
    parameters={
        "type": "object",
        "properties": {},
        "required": [],
    },
    risk="safe",
)
