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

Browser dedup fix:
    Launching chrome.exe/msedge.exe again with NO url/tab argument makes
    the browser's own singleton logic open a brand-new window, even when
    an instance is already running with tabs open. This previously caused
    "open chrome" (as a lead-in to "then switch tabs / search there") to
    spawn a second, unrelated window -- so subsequent tab-switching and
    searching acted on the wrong (freshly created) window instead of the
    user's existing one. open_application now checks for an already-open
    chrome.exe/msedge.exe window first and, if found, simply brings that
    window forward instead of launching a duplicate.
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
from core.window_focus import force_activate_window

_user32 = ctypes.windll.user32
_kernel32 = ctypes.windll.kernel32

_PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
_SW_RESTORE = 9
_SW_MINIMIZE = 6
_SW_MAXIMIZE = 3
_VK_MENU = 0x12
_KEYEVENTF_KEYUP = 0x0002
_SPI_SETDESKWALLPAPER = 0x0014
_SPIF_UPDATEINIFILE = 0x01
_SPIF_SENDCHANGE = 0x02

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

# Browser executables that must never be launched a second time while an
# instance is already open -- see the module docstring's "Browser dedup
# fix" note above.
_BROWSER_PROCESS_NAMES = {"chrome.exe", "msedge.exe"}


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


def _find_existing_browser_window_hwnd(exe_name: str) -> int | None:
    """Find a visible top-level window already owned by a running browser
    process (chrome.exe/msedge.exe), so open_application can reuse it
    instead of launching a duplicate window. Launching chrome.exe/msedge.exe
    again with no URL argument triggers the browser's own default
    'open a new window' behavior even when an instance is already running
    -- this is what previously caused 'open chrome' to spawn a second
    window instead of bringing the existing one (with all its tabs)
    forward. Returns the first matching hwnd, or None if that browser
    isn't currently running with any visible window."""
    exe_name = exe_name.lower()
    for w in _enum_windows():
        if _get_process_image_name(w["pid"]) == exe_name:
            return w["hwnd"]
    return None


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

    # Browser dedup: if this is chrome/edge and a window is already open,
    # just bring it forward instead of launching a second window (see the
    # module docstring's "Browser dedup fix" note).
    browser_exe = os.path.basename(target).lower()
    if browser_exe in _BROWSER_PROCESS_NAMES and not path:
        existing_hwnd = _find_existing_browser_window_hwnd(browser_exe)
        if existing_hwnd is not None:
            if force_activate_window(existing_hwnd):
                return CapabilityResult.ok(
                    "open_application",
                    {"launched": target, "reused_existing_window": True, "hwnd": existing_hwnd},
                )
            # Activation failed for some reason; fall through to the
            # normal launch path below as a last-resort fallback.

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

def _enum_windows() -> list[dict]:
    """List every visible top-level window with a non-empty title."""
    windows = []

    def _callback(hwnd, _lparam):
        if not _user32.IsWindowVisible(hwnd):
            return True
        length = _user32.GetWindowTextLengthW(hwnd)
        if length == 0:
            return True
        buf = ctypes.create_unicode_buffer(length + 1)
        _user32.GetWindowTextW(hwnd, buf, length + 1)
        title = buf.value
        if title.strip():
            pid = wintypes.DWORD()
            _user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
            windows.append({"hwnd": hwnd, "title": title, "pid": pid.value})
        return True

    _user32.EnumWindows(_EnumWindowsProc(_callback), 0)
    return windows


def _find_hwnd(title_contains: str) -> int | None:
    needle = title_contains.lower()
    for w in _enum_windows():
        if needle in w["title"].lower():
            return w["hwnd"]
    return None


def _resolve_hwnd(hwnd: int | None, title: str | None) -> tuple[int | None, str | None]:
    """Returns (hwnd, error_message)."""
    if hwnd:
        if not _user32.IsWindow(hwnd):
            return None, f"No window exists with hwnd {hwnd}."
        return hwnd, None
    if title:
        found = _find_hwnd(title)
        if found is None:
            return None, f"No visible window found matching title '{title}'."
        return found, None
    return None, "Provide either hwnd or title."


def list_windows() -> CapabilityResult:
    """List all currently visible top-level windows with their titles, hwnd, and pid."""
    try:
        windows = _enum_windows()
    except OSError as exc:
        return CapabilityResult.fail("list_windows", f"Failed to enumerate windows: {exc}")
    return CapabilityResult.ok("list_windows", {"windows": windows, "count": len(windows)})


def get_active_window() -> CapabilityResult:
    """Get the title, hwnd, and pid of the currently focused window."""
    hwnd = _user32.GetForegroundWindow()
    if not hwnd:
        return CapabilityResult.fail("get_active_window", "No foreground window detected.")
    length = _user32.GetWindowTextLengthW(hwnd)
    buf = ctypes.create_unicode_buffer(length + 1)
    _user32.GetWindowTextW(hwnd, buf, length + 1)
    pid = wintypes.DWORD()
    _user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
    return CapabilityResult.ok("get_active_window", {
        "hwnd": hwnd, "title": buf.value, "pid": pid.value,
    })


def focus_window(hwnd: int | None = None, title: str | None = None) -> CapabilityResult:
    """Bring a window to the foreground, matched by hwnd or a substring of its title."""
    resolved_hwnd, err = _resolve_hwnd(hwnd, title)
    if err:
        return CapabilityResult.fail("focus_window", err)
    if not force_activate_window(resolved_hwnd):
        return CapabilityResult.fail("focus_window", "Window did not become foreground.")
    return CapabilityResult.ok("focus_window", {"hwnd": resolved_hwnd})


def minimize_window(hwnd: int | None = None, title: str | None = None) -> CapabilityResult:
    """Minimize a window matched by hwnd or title."""
    resolved_hwnd, err = _resolve_hwnd(hwnd, title)
    if err:
        return CapabilityResult.fail("minimize_window", err)
    _user32.ShowWindow(resolved_hwnd, _SW_MINIMIZE)
    return CapabilityResult.ok("minimize_window", {"hwnd": resolved_hwnd})


def maximize_window(hwnd: int | None = None, title: str | None = None) -> CapabilityResult:
    """Maximize a window matched by hwnd or title."""
    resolved_hwnd, err = _resolve_hwnd(hwnd, title)
    if err:
        return CapabilityResult.fail("maximize_window", err)
    force_activate_window(resolved_hwnd)  # best-effort; not fatal if it fails
    _user32.ShowWindow(resolved_hwnd, _SW_MAXIMIZE)
    return CapabilityResult.ok("maximize_window", {"hwnd": resolved_hwnd})


def close_window(hwnd: int | None = None, title: str | None = None) -> CapabilityResult:
    """Close a window gracefully (posts WM_CLOSE) matched by hwnd or title."""
    resolved_hwnd, err = _resolve_hwnd(hwnd, title)
    if err:
        return CapabilityResult.fail("close_window", err)
    WM_CLOSE = 0x0010
    _user32.PostMessageW(resolved_hwnd, WM_CLOSE, 0, 0)
    return CapabilityResult.ok("close_window", {"hwnd": resolved_hwnd})


def move_window(
    x: int, y: int, width: int, height: int,
    hwnd: int | None = None, title: str | None = None,
) -> CapabilityResult:
    """Move and/or resize a window matched by hwnd or title to (x, y, width, height)."""
    resolved_hwnd, err = _resolve_hwnd(hwnd, title)
    if err:
        return CapabilityResult.fail("move_window", err)
    SWP_NOZORDER = 0x0004
    ok = _user32.SetWindowPos(resolved_hwnd, 0, x, y, width, height, SWP_NOZORDER)
    if not ok:
        return CapabilityResult.fail("move_window", "SetWindowPos failed.")
    return CapabilityResult.ok("move_window", {
        "hwnd": resolved_hwnd, "x": x, "y": y, "width": width, "height": height,
    })


def get_screen_size() -> CapabilityResult:
    """Get the primary screen's resolution in pixels."""
    width = _user32.GetSystemMetrics(0)
    height = _user32.GetSystemMetrics(1)
    return CapabilityResult.ok("get_screen_size", {"width": width, "height": height})

def set_desktop_wallpaper(path: str) -> CapabilityResult:
    """Set the Windows desktop background/wallpaper to the given image file.

    Uses SystemParametersInfoW directly (SPI_SETDESKWALLPAPER), which is
    the real, immediate, user-visible way to change the wallpaper on
    Windows — no shell command or file write involved.
    """
    resolved = _resolve_target_path(path)

    if not os.path.isfile(resolved):
        return CapabilityResult.fail(
            "set_desktop_wallpaper", f"Image file not found: {resolved}"
        )

    valid_ext = {".bmp", ".jpg", ".jpeg", ".png"}
    if os.path.splitext(resolved)[1].lower() not in valid_ext:
        return CapabilityResult.fail(
            "set_desktop_wallpaper",
            f"Unsupported image type for wallpaper: {resolved}",
        )

    try:
        ok = _user32.SystemParametersInfoW(
            _SPI_SETDESKWALLPAPER,
            0,
            resolved,
            _SPIF_UPDATEINIFILE | _SPIF_SENDCHANGE,
        )
    except OSError as exc:
        return CapabilityResult.fail(
            "set_desktop_wallpaper", f"Failed to set wallpaper: {exc}"
        )

    if not ok:
        return CapabilityResult.fail(
            "set_desktop_wallpaper", "SystemParametersInfoW call failed."
        )

    return CapabilityResult.ok("set_desktop_wallpaper", {"wallpaper": resolved})


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

registry.register(
    name="list_windows", function=list_windows,
    description="List all visible top-level windows with their titles, hwnd, and pid.",
    parameters={"type": "object", "properties": {}, "required": []},
    risk="safe",
)

registry.register(
    name="get_active_window", function=get_active_window,
    description="Get the title, hwnd, and pid of the currently focused window.",
    parameters={"type": "object", "properties": {}, "required": []},
    risk="safe",
)

registry.register(
    name="focus_window", function=focus_window,
    description="Bring a window to the foreground, matched by hwnd or a substring of its title.",
    parameters={"type": "object", "properties": {
        "hwnd": {"type": ["integer", "null"]}, "title": {"type": ["string", "null"]},
    }, "required": []},
    risk="moderate",
)

registry.register(
    name="minimize_window", function=minimize_window,
    description="Minimize a window matched by hwnd or title.",
    parameters={"type": "object", "properties": {
        "hwnd": {"type": ["integer", "null"]}, "title": {"type": ["string", "null"]},
    }, "required": []},
    risk="moderate",
)

registry.register(
    name="maximize_window", function=maximize_window,
    description="Maximize a window matched by hwnd or title.",
    parameters={"type": "object", "properties": {
        "hwnd": {"type": ["integer", "null"]}, "title": {"type": ["string", "null"]},
    }, "required": []},
    risk="moderate",
)

registry.register(
    name="close_window", function=close_window,
    description="Close a window gracefully by posting WM_CLOSE, matched by hwnd or title.",
    parameters={"type": "object", "properties": {
        "hwnd": {"type": ["integer", "null"]}, "title": {"type": ["string", "null"]},
    }, "required": []},
    risk="high",
)

registry.register(
    name="move_window", function=move_window,
    description="Move and resize a window to a given position and size.",
    parameters={"type": "object", "properties": {
        "hwnd": {"type": ["integer", "null"]}, "title": {"type": ["string", "null"]},
        "x": {"type": "integer"}, "y": {"type": "integer"},
        "width": {"type": "integer"}, "height": {"type": "integer"},
    }, "required": ["x", "y", "width", "height"]},
    risk="moderate",
)

registry.register(
    name="get_screen_size", function=get_screen_size,
    description="Get the primary monitor's resolution in pixels.",
    parameters={"type": "object", "properties": {}, "required": []},
    risk="safe",
)
registry.register(
    name="set_desktop_wallpaper",
    function=set_desktop_wallpaper,
    description=(
        "Set/change the Windows desktop background (wallpaper) to a "
        "specific image file. Pass the full resolved absolute path to the "
        "image — e.g. after listing files in a folder and picking the "
        "1st/5th/etc one by sorted order. This directly applies the "
        "wallpaper immediately. Always use this capability for any "
        "request to change or set the desktop background/wallpaper — "
        "do NOT use open_application, run_command, execute_command, or "
        "write_file for this task."
    ),
    parameters={
        "type": "object",
        "properties": {
            "path": {
                "type": "string",
                "description": "Absolute path to the image file to set as wallpaper.",
            },
        },
        "required": ["path"],
    },
    risk="safe",
)
