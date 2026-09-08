"""
Mouse and keyboard control capabilities.
"""

from __future__ import annotations

import ctypes
import time

import pyautogui
import win32gui

from core.registry import registry
from core.result import CapabilityResult
from core.window_focus import resolve_target_window, focus_and_get_rect

pyautogui.FAILSAFE = True
pyautogui.PAUSE = 0.05


# ---------------------------------------------------------------------------
# Low-level SendInput helpers (more reliable than mouse_event for
# Electron/Chromium apps like VS Code, and for modern Win32 apps generally)
# ---------------------------------------------------------------------------

PUL = ctypes.POINTER(ctypes.c_ulong)


class MOUSEINPUT(ctypes.Structure):
    _fields_ = [
        ("dx", ctypes.c_long),
        ("dy", ctypes.c_long),
        ("mouseData", ctypes.c_ulong),
        ("dwFlags", ctypes.c_ulong),
        ("time", ctypes.c_ulong),
        ("dwExtraInfo", PUL),
    ]


class INPUT(ctypes.Structure):
    _fields_ = [("type", ctypes.c_ulong), ("mi", MOUSEINPUT)]


INPUT_MOUSE = 0
MOUSEEVENTF_MOVE = 0x0001
MOUSEEVENTF_ABSOLUTE = 0x8000
MOUSEEVENTF_WHEEL = 0x0800


def _send_mouse_input(dwFlags: int, dx: int = 0, dy: int = 0, mouseData: int = 0) -> None:
    extra = ctypes.c_ulong(0)
    ii = INPUT(
        type=INPUT_MOUSE,
        mi=MOUSEINPUT(dx, dy, mouseData, dwFlags, 0, ctypes.pointer(extra)),
    )
    ctypes.windll.user32.SendInput(1, ctypes.pointer(ii), ctypes.sizeof(ii))


def move_cursor_absolute(x: int, y: int) -> None:
    """Move the OS cursor using SendInput (absolute screen coordinates)."""
    screen_w = ctypes.windll.user32.GetSystemMetrics(0)
    screen_h = ctypes.windll.user32.GetSystemMetrics(1)
    abs_x = int(x * 65535 / screen_w)
    abs_y = int(y * 65535 / screen_h)
    _send_mouse_input(MOUSEEVENTF_MOVE | MOUSEEVENTF_ABSOLUTE, abs_x, abs_y)


def send_wheel(delta: int) -> None:
    """Send a single wheel notch via SendInput. delta=+120 up, -120 down."""
    _send_mouse_input(MOUSEEVENTF_WHEEL, mouseData=delta)


def get_wheel_scroll_lines() -> int:
    """Read the OS 'lines to scroll per wheel notch' setting (diagnostic use)."""
    lines = ctypes.c_uint()
    ctypes.windll.user32.SystemParametersInfoW(0x0068, 0, ctypes.byref(lines), 0)  # SPI_GETWHEELSCROLLLINES
    return lines.value


# ---------------------------------------------------------------------------
# Native window scroll (focus-aware, cursor-anchored)
# ---------------------------------------------------------------------------

def scroll_native_window(hint: str, direction: str, steps: int) -> CapabilityResult:
    """Scroll a resolved window (by title hint, or current foreground if hint is generic)."""
    direction = direction.strip().lower()
    if direction not in ("up", "down"):
        return CapabilityResult.fail("scroll_native_window", "direction must be 'up' or 'down'.")

    hwnd = resolve_target_window(hint)
    if not hwnd:
        return CapabilityResult.fail(
            "scroll_native_window", f"Couldn't find a window matching '{hint}'."
        )

    rect = focus_and_get_rect(hwnd)
    if not rect:
        return CapabilityResult.fail(
            "scroll_native_window", "Found the window but couldn't bring it to focus."
        )

    left, top, right, bottom = rect
    center_x, center_y = (left + right) // 2, (top + bottom) // 2

    original_pos = win32gui.GetCursorPos()

    # small nudge then settle on target, so hover/hit-testing registers properly
    move_cursor_absolute(center_x - 5, center_y - 5)
    time.sleep(0.05)
    move_cursor_absolute(center_x, center_y)
    time.sleep(0.08)

    delta = 120 if direction == "up" else -120
    sent = 0
    for _ in range(steps):
        send_wheel(delta)
        sent += 1
        time.sleep(0.04)  # avoid OS coalescing consecutive wheel messages

    move_cursor_absolute(*original_pos)

    title = win32gui.GetWindowText(hwnd)
    return CapabilityResult.ok(
        "scroll_native_window",
        {
            "window": title,
            "direction": direction,
            "steps": sent,
            "response": f"Scrolled '{title}' {direction} by {sent} notches.",
        },
    )


def move_mouse(x: int, y: int, duration: float = 0.1) -> CapabilityResult:
    """Move the mouse cursor to absolute screen coordinates (x, y)."""
    try:
        pyautogui.moveTo(x, y, duration=max(0.0, min(duration, 3.0)))
    except Exception as exc:
        return CapabilityResult.fail("move_mouse", f"Failed to move mouse: {exc}")
    return CapabilityResult.ok("move_mouse", {"x": x, "y": y})


def click_mouse(
    x: int | None = None, y: int | None = None,
    button: str = "left", clicks: int = 1,
) -> CapabilityResult:
    """Click the mouse at (x, y), or at the current cursor position if omitted."""
    button = button.strip().lower()
    if button not in ("left", "right", "middle"):
        return CapabilityResult.fail("click_mouse", "button must be 'left', 'right', or 'middle'.")
    try:
        pyautogui.click(x=x, y=y, clicks=clicks, button=button, interval=0.05)
    except Exception as exc:
        return CapabilityResult.fail("click_mouse", f"Failed to click: {exc}")
    return CapabilityResult.ok("click_mouse", {"x": x, "y": y, "button": button, "clicks": clicks})


def drag_mouse(start_x: int, start_y: int, end_x: int, end_y: int, duration: float = 0.3) -> CapabilityResult:
    """Press down at (start_x, start_y), drag to (end_x, end_y), and release."""
    try:
        pyautogui.moveTo(start_x, start_y)
        pyautogui.dragTo(end_x, end_y, duration=max(0.05, min(duration, 3.0)), button="left")
    except Exception as exc:
        return CapabilityResult.fail("drag_mouse", f"Failed to drag: {exc}")
    return CapabilityResult.ok("drag_mouse", {"start": [start_x, start_y], "end": [end_x, end_y]})


def scroll_mouse(amount: int, x: int | None = None, y: int | None = None) -> CapabilityResult:
    """Scroll the mouse wheel. Positive amount scrolls up, negative scrolls down."""
    try:
        if x is not None and y is not None:
            pyautogui.moveTo(x, y)
        pyautogui.scroll(amount)
    except Exception as exc:
        return CapabilityResult.fail("scroll_mouse", f"Failed to scroll: {exc}")
    return CapabilityResult.ok("scroll_mouse", {"amount": amount})


def type_text(text: str, interval: float = 0.02) -> CapabilityResult:
    """Type a string of text using the keyboard, character by character."""
    try:
        pyautogui.write(text, interval=max(0.0, min(interval, 0.5)))
    except Exception as exc:
        return CapabilityResult.fail("type_text", f"Failed to type text: {exc}")
    return CapabilityResult.ok("type_text", {"length": len(text)})


def press_key(key: str) -> CapabilityResult:
    """Press a single key (e.g. 'enter', 'tab', 'esc', 'f5')."""
    try:
        pyautogui.press(key)
    except Exception as exc:
        return CapabilityResult.fail("press_key", f"Failed to press key '{key}': {exc}")
    return CapabilityResult.ok("press_key", {"key": key})


def hotkey(keys: list[str]) -> CapabilityResult:
    """Press a key combination simultaneously (e.g. ['ctrl', 'c'] or ['alt', 'tab'])."""
    if not keys:
        return CapabilityResult.fail("hotkey", "No keys provided.")
    try:
        pyautogui.hotkey(*keys)
    except Exception as exc:
        return CapabilityResult.fail("hotkey", f"Failed to press hotkey {keys}: {exc}")
    return CapabilityResult.ok("hotkey", {"keys": keys})


registry.register(
    name="scroll_native_window", function=scroll_native_window,
    description="Scroll a specific window (by name hint like 'vs code', 'chrome') or the "
                "current foreground window if no specific hint is given, using real OS-level "
                "mouse wheel input anchored at the window's center.",
    parameters={"type": "object", "properties": {
        "hint": {"type": "string", "description": "Window name hint, or 'screen'/'window' for current foreground."},
        "direction": {"type": "string", "enum": ["up", "down"]},
        "steps": {"type": "integer", "description": "Number of wheel notches to send."},
    }, "required": ["hint", "direction", "steps"]},
    risk="moderate",
)

registry.register(
    name="move_mouse", function=move_mouse,
    description="Move the mouse cursor to absolute screen coordinates.",
    parameters={"type": "object", "properties": {
        "x": {"type": "integer"}, "y": {"type": "integer"},
        "duration": {"type": "number", "description": "Seconds to animate the move. Defaults to 0.1."},
    }, "required": ["x", "y"]},
    risk="moderate",
)

registry.register(
    name="click_mouse", function=click_mouse,
    description="Click the mouse at given coordinates, or the current position if omitted.",
    parameters={"type": "object", "properties": {
        "x": {"type": ["integer", "null"]}, "y": {"type": ["integer", "null"]},
        "button": {"type": "string", "enum": ["left", "right", "middle"]},
        "clicks": {"type": "integer", "description": "1 for single click, 2 for double click."},
    }, "required": []},
    risk="high",
)

registry.register(
    name="drag_mouse", function=drag_mouse,
    description="Click-and-drag the mouse from one point to another.",
    parameters={"type": "object", "properties": {
        "start_x": {"type": "integer"}, "start_y": {"type": "integer"},
        "end_x": {"type": "integer"}, "end_y": {"type": "integer"},
        "duration": {"type": "number"},
    }, "required": ["start_x", "start_y", "end_x", "end_y"]},
    risk="high",
)

registry.register(
    name="scroll_mouse", function=scroll_mouse,
    description="Scroll the mouse wheel up or down, optionally at a given position.",
    parameters={"type": "object", "properties": {
        "amount": {"type": "integer", "description": "Positive scrolls up, negative scrolls down."},
        "x": {"type": ["integer", "null"]}, "y": {"type": ["integer", "null"]},
    }, "required": ["amount"]},
    risk="safe",
)

registry.register(
    name="type_text", function=type_text,
    description="Type text using the keyboard at the current cursor/focus location.",
    parameters={"type": "object", "properties": {
        "text": {"type": "string"}, "interval": {"type": "number"},
    }, "required": ["text"]},
    risk="high",
)

registry.register(
    name="press_key", function=press_key,
    description="Press a single keyboard key, e.g. 'enter', 'tab', 'esc', 'f5'.",
    parameters={"type": "object", "properties": {"key": {"type": "string"}}, "required": ["key"]},
    risk="moderate",
)

registry.register(
    name="hotkey", function=hotkey,
    description=(
        "Press a combination of keys simultaneously, e.g. ['ctrl','c'] or "
        "['alt','tab']. WARNING: this sends input to whatever window "
        "currently has OS-level focus, NOT necessarily the app named in "
        "the request. Do NOT use this for switching/cycling browser tabs — "
        "use switch_tab_direction instead, since it force-activates the "
        "correct browser window first."
    ),
    parameters={"type": "object", "properties": {
        "keys": {"type": "array", "items": {"type": "string"}},
    }, "required": ["keys"]},
    risk="moderate",
)
