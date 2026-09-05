"""
Windows application launching capabilities.

Resolves friendly names to real executables or protocol URIs, checking
PATH first and falling back to known install locations. Launches
directly instead of via a shell command, so a missing app produces a
real failure instead of a silent no-op.
"""

from __future__ import annotations

import os
import subprocess

from core.app_paths import (
    FRIENDLY_APPS,
    PROTOCOL_APPS,
    resolve_executable,
    match_installed_app,
)
from core.registry import registry
from core.result import CapabilityResult


def open_application(name: str) -> CapabilityResult:
    """Launch a Windows application by friendly name, executable name, or full path."""
    key = name.strip().lower()

    if key in PROTOCOL_APPS:
        protocol = PROTOCOL_APPS[key]
        try:
            os.startfile(protocol)
        except OSError as exc:
            return CapabilityResult.fail("open_application", f"Failed to open '{name}': {exc}")
        return CapabilityResult.ok("open_application", {"launched": protocol})

    target = FRIENDLY_APPS.get(key, name)

    if os.path.isfile(target):
        try:
            os.startfile(target)
        except OSError as exc:
            return CapabilityResult.fail("open_application", f"Failed to launch '{target}': {exc}")
        return CapabilityResult.ok("open_application", {"launched": target})

    resolved = resolve_executable(target)
    if resolved is None:
        app_id = match_installed_app(name)
        if app_id is not None:
            try:
                os.startfile(f"shell:AppsFolder\\{app_id}")
            except OSError as exc:
                return CapabilityResult.fail("open_application", f"Failed to launch '{name}': {exc}")
            return CapabilityResult.ok("open_application", {"launched": app_id})

        return CapabilityResult.fail(
            "open_application",
            f"Could not find an application matching '{name}'. It may not be installed, "
            "or I don't recognize that name yet.",
        )

    try:
        subprocess.Popen([resolved])
    except OSError as exc:
        return CapabilityResult.fail("open_application", f"Failed to launch '{name}': {exc}")

    return CapabilityResult.ok("open_application", {"launched": resolved})


registry.register(
    name="open_application",
    function=open_application,
    description=(
        "Open/launch a Windows application by common name (e.g. 'notepad', "
        "'chrome', 'edge', 'calculator', 'task manager', 'camera', "
        "'settings') or by executable name/path. Use this for opening the "
        "app/browser itself, including 'a new chrome/edge window'."
    ),
    parameters={
        "type": "object",
        "properties": {
            "name": {"type": "string", "description": "Friendly app name, executable name, or full path to launch."},
        },
        "required": ["name"],
    },
    risk="safe",
)
