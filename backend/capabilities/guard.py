"""
Minimal protective guard against obviously catastrophic filesystem/process
operations. This is NOT the Safety Layer (that's Phase 7). It only blocks
a small set of unambiguously dangerous targets so early testing can't
accidentally damage the host machine.
"""

from __future__ import annotations

import os

_PROTECTED_DIR_PREFIXES = [
    p.lower() for p in [
        os.path.expandvars(r"%WINDIR%"),
        os.path.expandvars(r"%PROGRAMFILES%"),
        os.path.expandvars(r"%PROGRAMFILES(X86)%"),
        os.path.expandvars(r"%SYSTEMROOT%"),
    ]
    if p and "%" not in p
]

_PROTECTED_PROCESS_NAMES = {
    "explorer.exe",
    "csrss.exe",
    "winlogon.exe",
    "services.exe",
    "lsass.exe",
    "wininit.exe",
    "system",
}


def is_protected_path(path: str) -> bool:
    """True if the resolved path is a drive root or a critical system folder."""
    resolved = os.path.abspath(os.path.expanduser(os.path.expandvars(path))).lower()

    drive, tail = os.path.splitdrive(resolved)
    if drive and tail in ("", os.sep):
        return True  # e.g. "C:\" itself

    return any(resolved.startswith(prefix) for prefix in _PROTECTED_DIR_PREFIXES)


def is_protected_process(name: str) -> bool:
    """True if the process name is a critical OS process that must never be killed."""
    return name.strip().lower() in _PROTECTED_PROCESS_NAMES
