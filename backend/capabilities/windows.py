"""
Windows application launching capabilities.

A small friendly-name map covers common apps ("notepad", "chrome"...),
falling back to treating the input as a raw executable name/path that
Windows resolves itself.
"""

from __future__ import annotations

import os
import subprocess

from core.registry import registry
from core.result import CapabilityResult

_FRIENDLY_APPS = {
    "notepad": "notepad.exe",
    "calculator": "calc.exe",
    "calc": "calc.exe",
    "paint": "mspaint.exe",
    "explorer": "explorer.exe",
    "file explorer": "explorer.exe",
    "task manager": "taskmgr.exe",
    "cmd": "cmd.exe",
    "command prompt": "cmd.exe",
    "powershell": "powershell.exe",
    "chrome": "chrome.exe",
    "google chrome": "chrome.exe",
    "edge": "msedge.exe",
    "word": "winword.exe",
    "excel": "excel.exe",
    "settings": "ms-settings:",
}


def open_application(name: str) -> CapabilityResult:
    """Launch a Windows application by friendly name, executable name, or path."""
    key = name.strip().lower()
    target = _FRIENDLY_APPS.get(key, name)

    try:
        if target.startswith("ms-settings:") or os.path.isfile(target):
            os.startfile(target)
        else:
            subprocess.Popen(target, shell=True)
    except FileNotFoundError:
        return CapabilityResult.fail("open_application", f"Could not find application: {name}")
    except OSError as exc:
        return CapabilityResult.fail("open_application", f"Failed to launch '{name}': {exc}")

    return CapabilityResult.ok("open_application", {"launched": target})


registry.register(
    name="open_application",
    function=open_application,
    description=(
        "Open/launch a Windows application by common name (e.g. 'notepad', "
        "'chrome', 'calculator', 'task manager') or by executable name/path."
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
