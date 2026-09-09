"""
Working Context — Phase 8.

Tracks the "current file / folder / project / application / browser /
tab" that the user has been working with during a session, purely from
observing successful capability results as the Agent loop runs.

This lets JESSY resolve references like "open it", "add this to it",
or "run it" without the user having to repeat a full path or name on
every turn.

Deliberately NOT a general-purpose memory system: it holds only a
handful of "current X" slots plus a short rolling list of recent
actions, all scoped to a single session_id. See agent/agent.py for how
this gets folded into the messages sent to Groq (as a short summary,
never as a growing unbounded history).

Field-mapping notes:
    Capability names and their result payloads vary slightly across
    filesystem.py / windows.py / browser.py / editor.py. Rather than
    hardcoding one exact key per capability (fragile if a capability's
    return shape changes), each `update_from_result` branch tries a
    short list of likely keys, in order:
        result.data[<key>] -> arguments[<key>]
    and only writes a slot when something concrete is found. Missing
    or unrecognized shapes simply leave the existing slot untouched
    rather than erroring, since this is a best-effort convenience
    layer, not a critical-path feature. If you notice a specific
    capability isn't updating the right slot, check what keys its
    CapabilityResult.data actually uses and add them to the tuples
    below — nothing else in this file needs to change.
"""

from __future__ import annotations

import threading
from collections import deque
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Optional

from core.result import CapabilityResult, RiskLevel


# Capability name sets -> which "current X" slot each one should update.
# Extend these as new capabilities are added.
_FILE_CAPABILITIES = {
    "write_file",
    "append_to_file",
    "create_file_visual_in_vscode",
    "open_file_in_vscode",
    "read_file",
    "save_file_in_vscode",
}
_FOLDER_CAPABILITIES = {
    "create_folder_visual",
    "create_directory",
    "open_folder",
}
_PROJECT_CAPABILITIES = {
    "inspect_project",
    "detect_project_type",
    "run_project",
    "stop_project",
    "inspect_package_file",
    "inspect_git_status",
}
_APPLICATION_CAPABILITIES = {
    "open_application",
    "launch_application",
    "focus_application",
}
_BROWSER_TAB_CAPABILITIES = {
    "open_url",
    "navigate_active_tab",
    "new_browser_tab",
    "switch_tab_direction",
    "navigate_browser",
}

# Candidate key names to look for, checked in result.data first, then
# in the original call arguments, in order given.
_PATH_KEYS = ("path", "file_path", "folder_path", "project_path", "target")
_NAME_KEYS = ("name", "application", "app", "process_name")
_URL_KEYS = ("url", "current_url")
_BROWSER_KEYS = ("browser",)


def _first_present(
    sources: tuple[dict[str, Any], ...], keys: tuple[str, ...]
) -> Optional[str]:
    """Return the first truthy value found for any key, checked across
    sources in order (e.g. result.data before arguments)."""
    for source in sources:
        if not source:
            continue
        for key in keys:
            value = source.get(key)
            if value:
                return str(value)
    return None


@dataclass
class WorkingContext:
    """Per-session snapshot of what the user is currently working with."""

    current_file: Optional[str] = None
    current_folder: Optional[str] = None
    current_project: Optional[str] = None
    current_application: Optional[str] = None
    current_browser: Optional[str] = None
    current_browser_tab: Optional[str] = None
    recent_actions: deque = field(default_factory=lambda: deque(maxlen=10))

    def update_from_result(
        self, capability: str, arguments: dict[str, Any], result: CapabilityResult
    ) -> None:
        """
        Update the relevant 'current X' slot(s) from a capability call.

        recent_actions gets an entry regardless of success/failure (it's
        a log of what was attempted). The "current X" slots only ever
        get updated on success — a failed open_file must never make
        JESSY think that file is now "current".
        """
        self.recent_actions.append(
            {
                "action": capability,
                "success": result.success,
                "timestamp": datetime.now(timezone.utc).isoformat(),
            }
        )

        if not result.success and result.risk_level != RiskLevel.CONFIRMATION_REQUIRED:
            return

        data = result.data or {}

        if capability in _FILE_CAPABILITIES:
            value = _first_present((data, arguments), _PATH_KEYS)
            if value:
                self.current_file = value

        if capability in _FOLDER_CAPABILITIES:
            value = _first_present((data, arguments), _PATH_KEYS)
            if value:
                self.current_folder = value

        if capability in _PROJECT_CAPABILITIES:
            value = _first_present((data, arguments), _PATH_KEYS)
            if value:
                self.current_project = value

        if capability in _APPLICATION_CAPABILITIES:
            value = _first_present((data, arguments), _NAME_KEYS)
            if value:
                self.current_application = value

        if capability in _BROWSER_TAB_CAPABILITIES:
            browser_value = _first_present((data, arguments), _BROWSER_KEYS)
            if browser_value:
                self.current_browser = browser_value
            url_value = _first_present((data, arguments), _URL_KEYS)
            if url_value:
                self.current_browser_tab = url_value

    def as_prompt_context(self) -> Optional[str]:
        """
        Build a short, plain-text summary for the model to see, or None
        if there's nothing worth mentioning yet (keeps prompts clean
        for brand-new sessions instead of injecting an empty block).
        """
        lines: list[str] = []
        if self.current_file:
            lines.append(f"- Current file: {self.current_file}")
        if self.current_folder:
            lines.append(f"- Current folder: {self.current_folder}")
        if self.current_project:
            lines.append(f"- Current project: {self.current_project}")
        if self.current_application:
            lines.append(f"- Current application: {self.current_application}")
        if self.current_browser:
            tab = (
                f" (current tab: {self.current_browser_tab})"
                if self.current_browser_tab
                else ""
            )
            lines.append(f"- Current browser: {self.current_browser}{tab}")

        if not lines:
            return None

        return (
            "Working context from earlier in this conversation (use this to "
            "resolve references like 'it', 'that file', or 'the project' — "
            "do not restate it to the user unless they ask):\n"
            + "\n".join(lines)
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "current_file": self.current_file,
            "current_folder": self.current_folder,
            "current_project": self.current_project,
            "current_application": self.current_application,
            "current_browser": self.current_browser,
            "current_browser_tab": self.current_browser_tab,
            "recent_actions": list(self.recent_actions),
        }


# --- Per-session store ---------------------------------------------------
#
# Same tradeoff as agent.agent._pending_confirmations: plain in-memory
# dict at module scope. Only reliable with a single uvicorn worker and
# no --reload. See agent/agent.py's module docstring for the full
# explanation.

_contexts: dict[str, WorkingContext] = {}
_lock = threading.Lock()


def get_context(session_id: str) -> WorkingContext:
    """Return the WorkingContext for a session, creating one if needed."""
    with _lock:
        if session_id not in _contexts:
            _contexts[session_id] = WorkingContext()
        return _contexts[session_id]


def clear_context(session_id: str) -> None:
    """Drop the working context for a session (e.g. on 'new conversation')."""
    with _lock:
        _contexts.pop(session_id, None)
