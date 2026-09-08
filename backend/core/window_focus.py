"""
Shared window-foregrounding logic.

Windows' focus-stealing prevention silently denies foreground requests
from background processes (visible as a blinking taskbar icon while the
target window never actually comes forward). AttachThreadInput temporarily
borrows the currently-foreground thread's input permissions so our
activation request is treated as if it came from the already-focused app,
which Windows allows.

Used by both capabilities/browser.py (UIA window Controls) and
capabilities/windows.py (raw hwnd window management) so this fix only
ever needs to be maintained in one place.
"""

from __future__ import annotations

import ctypes
import time
import win32gui
import win32con
import win32api

_user32 = ctypes.windll.user32
_kernel32 = ctypes.windll.kernel32

_SW_RESTORE = 9


def force_activate_window(hwnd: int) -> bool:
    """Bring a window to the foreground reliably. Plain SetForegroundWindow/
    SwitchToThisWindow is often denied by Windows' focus-stealing
    prevention, so we use AttachThreadInput to borrow the foreground
    thread's input permissions. SW_RESTORE is only ever sent when the
    window is ACTUALLY iconic — sending it unconditionally on an
    already-normal window can race with the app's own frame animation
    and cause it to flip into minimized state right after being
    activated. A final verify-and-recover pass guards against that.
    Returns True only if the window ends up both foreground and not
    minimized."""
    if not hwnd or not _user32.IsWindow(hwnd):
        return False

    if _user32.IsIconic(hwnd):
        _user32.ShowWindow(hwnd, _SW_RESTORE)
        time.sleep(0.15)

    if _user32.GetForegroundWindow() == hwnd and not _user32.IsIconic(hwnd):
        time.sleep(0.1)
        return True

    fg_hwnd = _user32.GetForegroundWindow()
    fg_thread = _user32.GetWindowThreadProcessId(fg_hwnd, None)
    target_thread = _user32.GetWindowThreadProcessId(hwnd, None)
    current_thread = _kernel32.GetCurrentThreadId()

    attached_fg = False
    attached_target = False
    try:
        if fg_thread and fg_thread != current_thread:
            attached_fg = bool(_user32.AttachThreadInput(current_thread, fg_thread, True))
        if target_thread and target_thread != current_thread:
            attached_target = bool(_user32.AttachThreadInput(current_thread, target_thread, True))

        _user32.BringWindowToTop(hwnd)
        if _user32.IsIconic(hwnd):
            _user32.ShowWindow(hwnd, _SW_RESTORE)
        _user32.SetForegroundWindow(hwnd)
    except Exception:
        pass
    finally:
        if attached_fg:
            try:
                _user32.AttachThreadInput(current_thread, fg_thread, False)
            except Exception:
                pass
        if attached_target:
            try:
                _user32.AttachThreadInput(current_thread, target_thread, False)
            except Exception:
                pass

    time.sleep(0.15)

    if _user32.IsIconic(hwnd):
        _user32.ShowWindow(hwnd, _SW_RESTORE)
        time.sleep(0.15)
        _user32.SetForegroundWindow(hwnd)
        time.sleep(0.1)

    return _user32.GetForegroundWindow() == hwnd and not _user32.IsIconic(hwnd)

def get_foreground_window():
    """Return hwnd of whatever window is truly active right now."""
    return win32gui.GetForegroundWindow()

def find_window_by_hint(hint: str):
    """Find a top-level window whose title contains the hint (case-insensitive)."""
    hint = hint.lower()
    matches = []

    def _cb(hwnd, _):
        if win32gui.IsWindowVisible(hwnd) and win32gui.GetWindowText(hwnd):
            title = win32gui.GetWindowText(hwnd).lower()
            if hint in title:
                matches.append(hwnd)
    win32gui.EnumWindows(_cb, None)
    return matches[0] if matches else None

def resolve_target_window(hint: str | None):
    """
    hint examples: 'chrome', 'vs code', None/'window'/'screen' -> current foreground.
    Returns hwnd or None.
    """
    if not hint or hint.strip().lower() in ("window", "screen", "this", "current"):
        return get_foreground_window()

    aliases = {
        "vs code": "Visual Studio Code",
        "vscode": "Visual Studio Code",
        "chrome": "Chrome",
        "crome": "Chrome",
    }
    title_hint = aliases.get(hint.strip().lower(), hint)
    return find_window_by_hint(title_hint)

def focus_and_get_rect(hwnd):
    """Bring window to front and return its client rect, or None on failure."""
    if not hwnd or not win32gui.IsWindow(hwnd):
        return None
    if win32gui.IsIconic(hwnd):
        win32gui.ShowWindow(hwnd, win32con.SW_RESTORE)
    try:
        win32gui.SetForegroundWindow(hwnd)
    except Exception:
        return None
    time.sleep(0.15)  # let the OS actually switch focus
    if win32gui.GetForegroundWindow() != hwnd:
        return None  # focus failed — don't lie about success
    left, top, right, bottom = win32gui.GetWindowRect(hwnd)
    return (left, top, right, bottom)