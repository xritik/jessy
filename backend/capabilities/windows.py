"""
Windows application launching capabilities.

Resolves friendly names to real executables or protocol URIs, checking
PATH first and falling back to known install locations. Launches
directly instead of via a shell command, so a missing app produces a
real failure instead of a silent no-op.

After every launch, briefly forces the new window to the foreground.
Without this, a window opened by a background process (like this server)
is blocked by Windows' focus-stealing prevention and only flashes in the
taskbar until the user clicks it themselves.
"""

from __future__ import annotations

import ctypes
import os
import subprocess
import time
from ctypes import wintypes

from core.app_paths import (
    FRIENDLY_APPS,
    PROTOCOL_APPS,
    resolve_executable,
    match_installed_app,
    match_settings_page,
)
from core.registry import registry
from core.result import CapabilityResult

_user32 = ctypes.windll.user32
_kernel32 = ctypes.windll.kernel32

_PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
_SW_RESTORE = 9
_VK_MENU = 0x12
_KEYEVENTF_KEYUP = 0x0002

_EnumWindowsProc = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)

# Best-known background process names behind each protocol app, used to
# find and foreground the right window after launch. Settings is the one
# that matters most here and is reliably "SystemSettings.exe" on Win10/11.
PROTOCOL_APP_PROCESS_HINTS: dict[str, set[str]] = {
    "settings": {"systemsettings.exe"},
    "camera": {"windowscamera.exe"},
    "photos": {"microsoft.photos.exe"},
    "calculator app": {"calculator.exe"},
    "mail": {"hxmail.exe", "olk.exe"},
    "calendar": {"hxcalendarappimm.exe", "hxcalendar.exe"},
}


def _resolve_target_path(path: str) -> str:
    return os.path.abspath(os.path.expanduser(os.path.expandvars(path)))


def _prime_foreground_focus() -> None:
    """Tap the Alt key to reset Windows' foreground-lock timer.

    Windows normally refuses to let a window opened by a background
    process steal focus -- it just flashes in the taskbar instead of
    appearing. A synthetic key press resets that lock so the window
    we're about to open is allowed to come forward.
    """
    try:
        _user32.keybd_event(_VK_MENU, 0, 0, 0)
        _user32.keybd_event(_VK_MENU, 0, _KEYEVENTF_KEYUP, 0)
    except OSError:
        pass


def _get_process_image_name(pid: int) -> str:
    """Return the lowercase executable filename owning the given PID."""
    handle = _kernel32.OpenProcess(_PROCESS_QUERY_LIMITED_INFORMATION, False, pid)
    if not handle:
        return ""
    try:
        buf = ctypes.create_unicode_buffer(260)
        size = wintypes.DWORD(260)
        ok = _kernel32.QueryFullProcessImageNameW(handle, 0, buf, ctypes.byref(size))
        return os.path.basename(buf.value).lower() if ok else ""
    except OSError:
        return ""
    finally:
        _kernel32.CloseHandle(handle)


def _bring_process_window_to_front(exe_names: set[str], timeout: float = 3.0) -> bool:
    """Poll briefly for a visible top-level window owned by one of the
    given process executables, then force it to the foreground."""
    if not exe_names:
        return False

    deadline = time.time() + timeout
    target_hwnd = None

    def _callback(hwnd, _lparam):
        nonlocal target_hwnd
        if not _user32.IsWindowVisible(hwnd):
            return True
        if _user32.GetWindowTextLengthW(hwnd) == 0:
            return True
        pid = wintypes.DWORD()
        _user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
        if _get_process_image_name(pid.value) in exe_names:
            target_hwnd = hwnd
            return False
        return True

    proc = _EnumWindowsProc(_callback)

    while time.time() < deadline and target_hwnd is None:
        _user32.EnumWindows(proc, 0)
        if target_hwnd is None:
            time.sleep(0.15)

    if target_hwnd is None:
        return False

    _prime_foreground_focus()
    _user32.ShowWindow(target_hwnd, _SW_RESTORE)
    _user32.SetForegroundWindow(target_hwnd)
    return True


def open_application(name: str, path: str | None = None) -> CapabilityResult:
    """Launch a Windows application by friendly name, executable name, or
    full path. If `path` is given, the app is launched directly at/with
    that file or folder as a CLI argument (e.g. opening VS Code at a
    specific project folder) — only supported for PATH/exe-resolved apps,
    not Store/UWP apps launched via shell:AppsFolder."""
    key = name.strip().lower()

    # Specific Settings pages (About This PC, Windows Update, etc.) take
    # priority over the generic 'settings' protocol entry, so a specific
    # request doesn't just land on the root Settings page.
    settings_uri = match_settings_page(name)
    if settings_uri:
        try:
            _prime_foreground_focus()
            os.startfile(settings_uri)
        except OSError as exc:
            return CapabilityResult.fail("open_application", f"Failed to open '{name}': {exc}")
        _bring_process_window_to_front({"systemsettings.exe"}, timeout=4.0)
        return CapabilityResult.ok("open_application", {"launched": settings_uri})

    if key in PROTOCOL_APPS:
        protocol = PROTOCOL_APPS[key]
        try:
            _prime_foreground_focus()
            os.startfile(protocol)
        except OSError as exc:
            return CapabilityResult.fail("open_application", f"Failed to open '{name}': {exc}")
        _bring_process_window_to_front(PROTOCOL_APP_PROCESS_HINTS.get(key, set()), timeout=3.0)
        return CapabilityResult.ok("open_application", {"launched": protocol})

    target = FRIENDLY_APPS.get(key, name)

    if os.path.isfile(target) and not path:
        try:
            _prime_foreground_focus()
            os.startfile(target)
        except OSError as exc:
            return CapabilityResult.fail("open_application", f"Failed to launch '{target}': {exc}")
        _bring_process_window_to_front({os.path.basename(target).lower()}, timeout=3.0)
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
                _prime_foreground_focus()
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
        _prime_foreground_focus()
        if path:
            subprocess.Popen([resolved, _resolve_target_path(path)])
        else:
            subprocess.Popen([resolved])
    except OSError as exc:
        return CapabilityResult.fail("open_application", f"Failed to launch '{name}': {exc}")

    _bring_process_window_to_front({os.path.basename(resolved).lower()}, timeout=3.0)
    return CapabilityResult.ok("open_application", {"launched": resolved, "path": path})


registry.register(
    name="open_application",
    function=open_application,
    description=(
        "Open/launch a Windows application by common name (e.g. 'notepad', "
        "'chrome', 'edge', 'calculator', 'task manager', 'camera', "
        "'settings', 'vscode'), by a specific Settings page (e.g. 'about "
        "this pc', 'windows update', 'display settings', 'wifi settings'), "
        "or by executable name/path. Use this for opening the app itself, "
        "including 'a new chrome/edge window'. If the user wants the app "
        "to open AT a specific file or folder (e.g. 'open the jessy folder "
        "with VS Code'), pass that folder/file's resolved path as 'path' — "
        "this opens the app directly there in ONE call. Do not call "
        "open_folder separately in that case; only use open_folder when "
        "the user wants File Explorer, not another app."
    ),
    parameters={
        "type": "object",
        "properties": {
            "name": {"type": "string", "description": "Friendly app name, executable name, Settings page name, or full path to launch."},
            "path": {
                "type": ["string", "null"],
                "description": "Optional file or folder path to open the app at/with, e.g. a project folder for VS Code. Omit or pass null to just launch the app normally.",
            },
        },
        "required": ["name"],
    },
    risk="safe",
)
