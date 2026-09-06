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


def _resolve_target_path(path: str) -> str:
    return os.path.abspath(os.path.expanduser(os.path.expandvars(path)))


def open_application(name: str, path: str | None = None) -> CapabilityResult:
    """Launch a Windows application by friendly name, executable name, or
    full path. If `path` is given, the app is launched directly at/with
    that file or folder as a CLI argument (e.g. opening VS Code at a
    specific project folder) — only supported for PATH/exe-resolved apps,
    not Store/UWP apps launched via shell:AppsFolder."""
    key = name.strip().lower()

    if key in PROTOCOL_APPS:
        protocol = PROTOCOL_APPS[key]
        try:
            os.startfile(protocol)
        except OSError as exc:
            return CapabilityResult.fail("open_application", f"Failed to open '{name}': {exc}")
        return CapabilityResult.ok("open_application", {"launched": protocol})

    target = FRIENDLY_APPS.get(key, name)

    if os.path.isfile(target) and not path:
        try:
            os.startfile(target)
        except OSError as exc:
            return CapabilityResult.fail("open_application", f"Failed to launch '{target}': {exc}")
        return CapabilityResult.ok("open_application", {"launched": target})

    resolved = resolve_executable(target)
    if resolved is None:
        app_id = match_installed_app(name)
        if app_id is not None:
            if path:
                return CapabilityResult.fail(
                    "open_application",
                    f"'{name}' was found as an installed app, but it can't be "
                    "launched with a target file/folder this way. Try opening "
                    "it without a path, or open the folder separately.",
                )
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
        if path:
            subprocess.Popen([resolved, _resolve_target_path(path)])
        else:
            subprocess.Popen([resolved])
    except OSError as exc:
        return CapabilityResult.fail("open_application", f"Failed to launch '{name}': {exc}")

    return CapabilityResult.ok("open_application", {"launched": resolved, "path": path})


registry.register(
    name="open_application",
    function=open_application,
    description=(
        "Open/launch a Windows application by common name (e.g. 'notepad', "
        "'chrome', 'edge', 'calculator', 'task manager', 'camera', "
        "'settings', 'vscode') or by executable name/path. Use this for "
        "opening the app itself, including 'a new chrome/edge window'. "
        "If the user wants the app to open AT a specific file or folder "
        "(e.g. 'open the jessy folder with VS Code'), pass that folder/"
        "file's resolved path as 'path' — this opens the app directly "
        "there in ONE call. Do not call open_folder separately in that "
        "case; only use open_folder when the user wants File Explorer, "
        "not another app."
    ),
    parameters={
        "type": "object",
        "properties": {
            "name": {"type": "string", "description": "Friendly app name, executable name, or full path to launch."},
            "path": {
                "type": ["string", "null"],
                "description": "Optional file or folder path to open the app at/with, e.g. a project folder for VS Code. Omit or pass null to just launch the app normally.",
            },
        },
        "required": ["name"],
    },
    risk="safe",
)
