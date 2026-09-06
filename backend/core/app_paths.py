"""
Shared logic for resolving friendly app names to real Windows executables
or protocol URIs, so both windows.py and browser.py stay consistent.
"""

from __future__ import annotations

import os
import shutil
import json
import time
import subprocess
from difflib import get_close_matches

FRIENDLY_APPS = {
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
    "chrome browser": "chrome.exe",
    "crome": "chrome.exe",
    "edge": "msedge.exe",
    "microsoft edge": "msedge.exe",
    "edge browser": "msedge.exe",
    "word": "winword.exe",
    "microsoft word": "winword.exe",
    "excel": "excel.exe",
    "microsoft excel": "excel.exe",
    "powerpoint": "powerpnt.exe",
    "microsoft powerpoint": "powerpnt.exe",
    "vscode": "code.cmd",
    "vs code": "code.cmd",
    "visual studio code": "code.cmd",
    "code": "code.cmd",
}


PROTOCOL_APPS = {
    "settings": "ms-settings:",
    "camera": "microsoft.windows.camera:",
    "photos": "microsoft.windows.photos:",
    "calculator app": "calculator:",
    "mail": "outlookmail:",
    "calendar": "outlookcal:",
}

# Specific Settings sub-pages, keyed by the phrase a user is likely to say.
# Matched by substring (either direction), preferring the longest/most
# specific key, so "about this pc" wins over the shorter "about", and
# "update window" (even with a typo) still resolves to Windows Update
# instead of falling back to the generic root Settings page.
SETTINGS_PAGES = {
    "settings": "ms-settings:",
    "about": "ms-settings:about",
    "about this pc": "ms-settings:about",
    "system": "ms-settings:system",
    "display": "ms-settings:display",
    "sound": "ms-settings:sound",
    "notifications": "ms-settings:notifications",
    "power": "ms-settings:powersleep",
    "power and sleep": "ms-settings:powersleep",
    "battery": "ms-settings:batterysaver",
    "storage": "ms-settings:storagesense",
    "bluetooth": "ms-settings:bluetooth",
    "devices": "ms-settings:bluetooth",
    "network": "ms-settings:network",
    "wifi": "ms-settings:network-wifi",
    "wi-fi": "ms-settings:network-wifi",
    "personalization": "ms-settings:personalization",
    "background": "ms-settings:personalization-background",
    "wallpaper": "ms-settings:personalization-background",
    "lock screen": "ms-settings:lockscreen",
    "themes": "ms-settings:themes",
    "apps": "ms-settings:appsfeatures",
    "apps and features": "ms-settings:appsfeatures",
    "accounts": "ms-settings:accounts",
    "date and time": "ms-settings:dateandtime",
    "time": "ms-settings:dateandtime",
    "language": "ms-settings:regionlanguage",
    "gaming": "ms-settings:gaming",
    "ease of access": "ms-settings:easeofaccess",
    "accessibility": "ms-settings:easeofaccess",
    "privacy": "ms-settings:privacy",
    "windows update": "ms-settings:windowsupdate",
    "update": "ms-settings:windowsupdate",
    "recovery": "ms-settings:recovery",
    "activation": "ms-settings:activation",
    "troubleshoot": "ms-settings:troubleshoot",
}


def match_friendly_name(name: str) -> str | None:
    """Match a name against FRIENDLY_APPS exactly first, then by substring."""
    key = name.strip().lower()
    if key in FRIENDLY_APPS:
        return FRIENDLY_APPS[key]
    for friendly_key, exe in FRIENDLY_APPS.items():
        if friendly_key in key or key in friendly_key:
            return exe
    return None


def match_protocol_app(name: str) -> str | None:
    key = name.strip().lower()
    if key in PROTOCOL_APPS:
        return PROTOCOL_APPS[key]
    for friendly_key, proto in PROTOCOL_APPS.items():
        if friendly_key in key or key in friendly_key:
            return proto
    return None


def match_settings_page(name: str) -> str | None:
    """Match free-form text (e.g. 'about this pc', 'update window',
    'wifi settings') to the correct ms-settings: deep-link URI for that
    specific page, instead of always falling back to the generic root
    Settings page. Prefers the longest (most specific) matching key."""
    key = name.strip().lower()
    if not key:
        return None

    best_key = None
    for page_key in SETTINGS_PAGES:
        if page_key in key or key in page_key:
            if best_key is None or len(page_key) > len(best_key):
                best_key = page_key

    return SETTINGS_PAGES[best_key] if best_key else None


def resolve_executable(exe_name: str) -> str | None:
    """Find a real path for an executable name via PATH, then known install locations."""
    found_on_path = shutil.which(exe_name)
    if found_on_path:
        return found_on_path

    for candidate in KNOWN_APP_PATHS.get(exe_name.lower(), []):
        expanded = os.path.expandvars(candidate)
        if os.path.isfile(expanded):
            return expanded

    return None


KNOWN_APP_PATHS = {
    "chrome.exe": [
        r"%PROGRAMFILES%\Google\Chrome\Application\chrome.exe",
        r"%PROGRAMFILES(X86)%\Google\Chrome\Application\chrome.exe",
        r"%LOCALAPPDATA%\Google\Chrome\Application\chrome.exe",
    ],
    "msedge.exe": [
        r"%PROGRAMFILES(X86)%\Microsoft\Edge\Application\msedge.exe",
        r"%PROGRAMFILES%\Microsoft\Edge\Application\msedge.exe",
    ],
    "winword.exe": [
        r"%PROGRAMFILES%\Microsoft Office\root\Office16\WINWORD.EXE",
        r"%PROGRAMFILES(X86)%\Microsoft Office\root\Office16\WINWORD.EXE",
        r"%PROGRAMFILES%\Microsoft Office\Office16\WINWORD.EXE",
    ],
    "excel.exe": [
        r"%PROGRAMFILES%\Microsoft Office\root\Office16\EXCEL.EXE",
        r"%PROGRAMFILES(X86)%\Microsoft Office\root\Office16\EXCEL.EXE",
        r"%PROGRAMFILES%\Microsoft Office\Office16\EXCEL.EXE",
    ],
    "powerpnt.exe": [
        r"%PROGRAMFILES%\Microsoft Office\root\Office16\POWERPNT.EXE",
        r"%PROGRAMFILES(X86)%\Microsoft Office\root\Office16\POWERPNT.EXE",
        r"%PROGRAMFILES%\Microsoft Office\Office16\POWERPNT.EXE",
    ],
}


# ---------------------------------------------------------------------------
# Live fallback: query Windows' own app registry (Get-StartApps) for anything
# not covered by the static maps above. Covers UWP/Store apps (Voice Recorder,
# Calculator app, Camera, etc.) and any newly installed app with zero
# maintenance required on our part.
# ---------------------------------------------------------------------------

_app_cache: dict = {"data": None, "timestamp": 0.0}
_CACHE_TTL = 600  # seconds


def get_installed_apps() -> dict[str, str]:
    """Return {app_name_lower: app_user_model_id} for every app Windows knows about.

    Queries `Get-StartApps`, the same source Start Menu search uses. Cached
    briefly so we don't spawn PowerShell on every single request.
    """
    now = time.time()
    if _app_cache["data"] is not None and (now - _app_cache["timestamp"] < _CACHE_TTL):
        return _app_cache["data"]

    app_map: dict[str, str] = {}
    try:
        result = subprocess.run(
            ["powershell", "-NoProfile", "-Command",
             "Get-StartApps | Select-Object Name, AppID | ConvertTo-Json"],
            capture_output=True, text=True, timeout=10,
        )
        raw = json.loads(result.stdout)
        if isinstance(raw, dict):  # PowerShell unwraps single-item arrays to a bare object
            raw = [raw]
        app_map = {entry["Name"].strip().lower(): entry["AppID"] for entry in raw}
    except Exception:
        pass  # fall back to empty map; caller handles the miss gracefully

    _app_cache["data"] = app_map
    _app_cache["timestamp"] = now
    return app_map


def match_installed_app(name: str) -> str | None:
    """Fuzzy-match a name against the live list of installed Windows apps.

    Returns the AppUserModelID (launchable via `shell:AppsFolder\\<id>`),
    or None if nothing close enough was found.
    """
    apps = get_installed_apps()
    key = name.strip().lower()

    if key in apps:
        return apps[key]

    matches = get_close_matches(key, apps.keys(), n=1, cutoff=0.6)
    if matches:
        return apps[matches[0]]

    return None
