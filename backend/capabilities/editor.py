"""
Visual, on-screen file and folder creation.

Unlike write_file/create_directory (which perform instant, invisible disk
I/O), these capabilities drive the real Windows Explorer and VS Code UI
via keyboard/window automation, so the user watches folders get created,
VS Code open, a file get made, and its content appear -- the same way a
person would do it by hand.
"""

from __future__ import annotations

import os
import subprocess
import time

import pyautogui
import win32clipboard

from capabilities.filesystem import _resolve
from capabilities.windows import open_application
from core.registry import registry
from core.result import CapabilityResult
from core.window_focus import find_window_by_hint, force_activate_window

pyautogui.FAILSAFE = True


def _set_clipboard(text: str) -> None:
    win32clipboard.OpenClipboard()
    try:
        win32clipboard.EmptyClipboard()
        win32clipboard.SetClipboardText(text, win32clipboard.CF_UNICODETEXT)
    finally:
        win32clipboard.CloseClipboard()


def _focus_vscode(timeout: float = 4.0) -> bool:
    deadline = time.time() + timeout
    hwnd = None
    while time.time() < deadline:
        hwnd = find_window_by_hint("Visual Studio Code")
        if hwnd:
            break
        time.sleep(0.2)
    if not hwnd:
        return False
    return force_activate_window(hwnd)


def create_folder_visual(name: str, parent: str = "~/Desktop") -> CapabilityResult:
    """Create a new folder by opening File Explorer at the parent location
    (Desktop by default) and driving the real 'New Folder' flow live, so
    the user watches the folder actually appear -- instead of it being
    created silently on disk and only shown afterward."""
    resolved_parent = _resolve(parent)
    if not os.path.isdir(resolved_parent):
        return CapabilityResult.fail("create_folder_visual", f"Parent folder does not exist: {resolved_parent}")

    target_path = os.path.join(resolved_parent, name)
    if os.path.exists(target_path):
        return CapabilityResult.fail("create_folder_visual", f"'{name}' already exists in {resolved_parent}.")

    try:
        subprocess.Popen(["explorer.exe", resolved_parent])
    except OSError as exc:
        return CapabilityResult.fail("create_folder_visual", f"Failed to open Explorer: {exc}")

    time.sleep(1.2)  # let the Explorer window appear

    hint = os.path.basename(resolved_parent) or resolved_parent
    hwnd = find_window_by_hint(hint)
    if hwnd:
        force_activate_window(hwnd)
    time.sleep(0.4)

    pyautogui.hotkey("ctrl", "shift", "n")
    time.sleep(0.6)
    pyautogui.write(name, interval=0.03)
    pyautogui.press("enter")
    time.sleep(0.4)

    if not os.path.isdir(target_path):
        return CapabilityResult.fail(
            "create_folder_visual",
            f"The New Folder command ran, but '{target_path}' wasn't found afterward -- "
            "the Explorer window may not have had focus.",
        )

    return CapabilityResult.ok("create_folder_visual", {"path": target_path})


def create_file_visual_in_vscode(filename: str, content: str, folder: str = "~/Desktop") -> CapabilityResult:
    """Open VS Code on the given folder (Desktop by default), create a new
    file there through the real UI (new tab, Save As with the given name),
    and type its content in line by line so the user watches it happen --
    instead of writing the file to disk first and only opening it after."""
    resolved_folder = _resolve(folder)
    if not os.path.isdir(resolved_folder):
        return CapabilityResult.fail("create_file_visual_in_vscode", f"Folder does not exist: {resolved_folder}")

    target_path = os.path.join(resolved_folder, filename)
    if os.path.exists(target_path):
        return CapabilityResult.fail(
            "create_file_visual_in_vscode", f"'{filename}' already exists in {resolved_folder}."
        )

    launch = open_application("vscode", path=resolved_folder)
    if not launch.success:
        return CapabilityResult.fail("create_file_visual_in_vscode", f"Failed to open VS Code: {launch.error}")

    time.sleep(1.5)  # Electron startup is slow; give it a moment before driving it
    if not _focus_vscode():
        return CapabilityResult.fail("create_file_visual_in_vscode", "VS Code window never came to focus.")

    # New untitled tab
    pyautogui.hotkey("ctrl", "n")
    time.sleep(0.5)

    # Save As -> native Windows dialog; type the full path directly into it
    pyautogui.hotkey("ctrl", "shift", "s")
    time.sleep(0.8)
    pyautogui.hotkey("ctrl", "a")
    pyautogui.write(target_path, interval=0.02)
    pyautogui.press("enter")
    time.sleep(1.0)

    if not os.path.isfile(target_path):
        return CapabilityResult.fail(
            "create_file_visual_in_vscode",
            f"Save As did not complete -- '{target_path}' was not found on disk afterward.",
        )

    if not _focus_vscode():
        return CapabilityResult.fail(
            "create_file_visual_in_vscode", "Lost focus on VS Code after saving the file."
        )

    # Type content in line by line (pasted per line to dodge autoclose/auto-indent corruption)
    lines = content.split("\n")
    for i, line in enumerate(lines):
        _set_clipboard(line)
        pyautogui.press("home")
        pyautogui.hotkey("shift", "end")
        pyautogui.hotkey("ctrl", "v")
        if i < len(lines) - 1:
            pyautogui.press("enter")
        time.sleep(0.12)  # visible pacing between lines

    pyautogui.hotkey("ctrl", "s")
    time.sleep(0.4)

    return CapabilityResult.ok(
        "create_file_visual_in_vscode",
        {"path": target_path, "lines_written": len(lines)},
    )


registry.register(
    name="create_folder_visual",
    function=create_folder_visual,
    description=(
        "Create a NEW FOLDER visibly by opening File Explorer at the parent "
        "location and driving the real 'New Folder' UI flow on screen, so "
        "the user watches it get created -- rather than create_directory, "
        "which creates it silently on disk with no visible action. Use "
        "this whenever the user wants to see/watch a folder being created, "
        "or whenever the request involves File Explorer or VS Code. "
        "Defaults to the Desktop if no parent location is given."
    ),
    parameters={
        "type": "object",
        "properties": {
            "name": {"type": "string", "description": "Name of the new folder."},
            "parent": {"type": "string", "description": "Parent folder to create it inside. Defaults to '~/Desktop'."},
        },
        "required": ["name"],
    },
    risk="moderate",
)

registry.register(
    name="create_file_visual_in_vscode",
    function=create_file_visual_in_vscode,
    description=(
        "Create a NEW FILE visibly inside VS Code: opens VS Code on the "
        "target folder, creates a new tab, saves it with the given "
        "filename via the real Save As dialog, then types the given "
        "content into the editor line by line so the user watches it "
        "happen on screen -- rather than write_file, which writes the "
        "file to disk silently with no visible action. Use this whenever "
        "the user wants to see/watch a file being created and written, or "
        "whenever VS Code is mentioned. Defaults to the Desktop if no "
        "folder is given."
    ),
    parameters={
        "type": "object",
        "properties": {
            "filename": {"type": "string", "description": "Name of the file to create, including extension."},
            "content": {"type": "string", "description": "Full text content to type into the file."},
            "folder": {"type": "string", "description": "Folder to create the file inside. Defaults to '~/Desktop'."},
        },
        "required": ["filename", "content"],
    },
    risk="moderate",
)
