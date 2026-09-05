"""
Browser capabilities: opening URLs with real control over tab vs window,
listing what's open, and closing a specific tab without touching others.

Windows' webbrowser module ignores the 'new window vs new tab' flag
entirely (it just calls os.startfile). To actually control this, we
resolve the browser executable and pass the URL/--new-window as real
command-line arguments.

Tab-level closing/listing uses UI Automation (UIA) since Chromium browsers
don't expose a "close this one tab" or "list open URLs" command to
external processes otherwise. Note: UIA tab titles are the page <title>,
NOT the URL, so we also read each window's address bar (an Edit control)
to get the actual URL for reliable matching.

Tabs are read from the window's TabControl container directly (not a
full recursive tree walk) so that background/non-active tabs are found
too, instead of only ever surfacing the currently active tab.
"""

from __future__ import annotations

import ctypes
import logging
import os
import re
import subprocess
import time

import uiautomation as auto

from core.app_paths import resolve_executable
from core.registry import registry
from core.result import CapabilityResult

logger = logging.getLogger("jessy.browser")

_BROWSER_EXES = {
    "chrome": "chrome.exe",
    "google chrome": "chrome.exe",
    "crome": "chrome.exe",
    "edge": "msedge.exe",
    "microsoft edge": "msedge.exe",
}


def open_url(url: str, browser: str | None = None, new_window: bool = False) -> CapabilityResult:
    """Open a URL. New tab in the given/running browser by default, or a real new window."""
    if url.startswith("file://") or (len(url) > 1 and url[1] == ":"):
        # This looks like a local filesystem path, not a website. Route it
        # to the folder opener instead of a browser — file:// URIs get
        # hijacked by the OS's default browser handler, not File Explorer.
        from capabilities.filesystem import open_folder
        local_path = url.replace("file:///", "").replace("file://", "")
        return open_folder(local_path)

    target = url if url.startswith(("http://", "https://")) else f"https://{url}"

    exe_name = _BROWSER_EXES.get((browser or "").strip().lower())

    if exe_name:
        resolved = resolve_executable(exe_name)
        if resolved is None:
            return CapabilityResult.fail("open_url", f"Could not find browser '{browser}' installed.")

        args = [resolved]
        if new_window:
            args.append("--new-window")
        args.append(target)

        try:
            subprocess.Popen(args)
        except OSError as exc:
            return CapabilityResult.fail("open_url", f"Failed to open URL: {exc}")

        return CapabilityResult.ok("open_url", {"url": target, "browser": browser, "new_window": new_window})

    try:
        os.startfile(target)
    except OSError as exc:
        return CapabilityResult.fail("open_url", f"Failed to open URL: {exc}")

    return CapabilityResult.ok("open_url", {"url": target, "browser": "default"})


registry.register(
    name="open_url",
    function=open_url,
    description=(
        "Open a URL/website. If the user names a specific browser (chrome/"
        "edge), pass it as 'browser' for reliable tab-vs-window control: "
        "without new_window it opens as a new TAB in that browser's "
        "running instance (or launches it if not running); with "
        "new_window=true it forces a genuinely separate new window. If no "
        "browser is named, it opens in the OS default browser. Do NOT use "
        "this for local folders/files — use open_folder instead."
    ),
    parameters={
        "type": "object",
        "properties": {
            "url": {"type": "string", "description": "URL or site to open, e.g. 'google.com'."},
            "browser": {"type": "string", "description": "Specific browser to use: 'chrome' or 'edge'. Omit if unspecified."},
            "new_window": {"type": "boolean", "description": "Force a new separate window instead of a new tab. Defaults to false."},
        },
        "required": ["url"],
    },
    risk="safe",
)


# ---------------------------------------------------------------------------
# Tab-level inspection & closing via UI Automation.
# ---------------------------------------------------------------------------

def _ensure_com_initialized() -> None:
    """UI Automation runs over COM, which must be initialized per-thread.
    Uvicorn may run this capability on a different worker thread each
    request, so we initialize defensively on every call. CoInitialize
    returns S_FALSE (harmless) if already initialized on this thread."""
    ctypes.windll.ole32.CoInitialize(None)


def _find_chromium_windows(browser: str | None = None) -> list[auto.Control]:
    """Find top-level Chromium-based browser windows (Chrome, Edge, Brave, etc.)."""
    root = auto.GetRootControl()
    windows = []
    for win in root.GetChildren():
        cls = win.ClassName or ""
        if "Chrome_WidgetWin" not in cls:
            continue
        name = (win.Name or "").lower()
        if browser:
            b = browser.strip().lower()
            if b == "chrome" and "chrome" not in name:
                continue
            if b == "edge" and "edge" not in name:
                continue
        windows.append(win)
    return windows


def _get_address_bar_text(win: auto.Control) -> str | None:
    """Read the URL currently shown in a window's address bar (reflects the
    active tab only). This is the actual URL, unlike tab titles."""
    try:
        edit = win.EditControl(searchDepth=12)
        if edit.Exists(0.5, 0.2):
            name = (edit.Name or "").lower()
            if "address" in name or "search" in name:
                pattern = edit.GetValuePattern()
                if pattern:
                    return pattern.Value
    except Exception:
        logger.exception("Failed reading address bar for window '%s'", win.Name)
    return None


def _collect_tab_items(win: auto.Control) -> list[auto.Control]:
    """Get every open tab (active and background) in a browser window by
    reading the tab strip container directly, instead of walking the
    entire UI tree (which includes the loaded page's own accessibility
    tree and can drown out/slow past the actual tab headers)."""
    try:
        tab_strip = win.TabControl(searchDepth=15)
        if tab_strip.Exists(1, 0.3):
            return [c for c in tab_strip.GetChildren() if c.ControlTypeName == "TabItemControl"]
    except Exception:
        logger.exception("Failed to locate tab strip for window '%s'", win.Name)
    return []


def list_browser_tabs(browser: str | None = None) -> CapabilityResult:
    """List every open tab's title (and the active tab's URL per window)."""
    _ensure_com_initialized()

    try:
        windows = _find_chromium_windows(browser)
    except Exception as exc:
        logger.exception("UIA window enumeration failed")
        return CapabilityResult.fail("list_browser_tabs", f"Could not scan browser windows: {exc}")

    if not windows:
        return CapabilityResult.fail(
            "list_browser_tabs", f"No open {browser or 'Chrome/Edge'} window was found."
        )

    windows_info = []
    for win in windows:
        tab_items = _collect_tab_items(win)
        titles = [t.Name for t in tab_items if t.Name]
        active_url = _get_address_bar_text(win)
        windows_info.append({
            "window_title": win.Name,
            "active_tab_url": active_url,
            "tab_count": len(titles),
            "tab_titles": titles,
        })

    return CapabilityResult.ok("list_browser_tabs", {"windows": windows_info})


registry.register(
    name="list_browser_tabs",
    function=list_browser_tabs,
    description=(
        "List currently open browser windows, their tab titles, and each "
        "window's active tab URL. Use this to see what's open before "
        "closing a specific tab, or when the user asks what tabs/URLs "
        "are open."
    ),
    parameters={
        "type": "object",
        "properties": {
            "browser": {"type": "string", "description": "Optional: 'chrome' or 'edge' to restrict the search."},
        },
        "required": [],
    },
    risk="safe",
)


def close_browser_tab(title_query: str, browser: str | None = None) -> CapabilityResult:
    """Close one specific tab (by matching part of its title or URL) without
    closing the rest of the window, tabs, or other browser windows."""
    query = title_query.strip()
    if not query:
        return CapabilityResult.fail("close_browser_tab", "No tab title/text to match was provided.")

    _ensure_com_initialized()

    try:
        windows = _find_chromium_windows(browser)
    except Exception as exc:
        logger.exception("UIA window enumeration failed")
        return CapabilityResult.fail("close_browser_tab", f"Could not scan browser windows: {exc}")

    if not windows:
        return CapabilityResult.fail(
            "close_browser_tab", f"No open {browser or 'Chrome/Edge'} window was found."
        )

    pattern = re.compile(re.escape(query), re.IGNORECASE)
    seen_titles: list[str] = []

    for win in windows:
        tab_items = _collect_tab_items(win)
        seen_titles.extend(t.Name for t in tab_items if t.Name)

        # 1. Try matching by tab title text.
        matched_tab = next((t for t in tab_items if t.Name and pattern.search(t.Name)), None)

        # 2. Fall back to matching by the window's active-tab URL (address bar).
        #    Reliable for single-tab windows, e.g. those opened via new_window=true.
        if matched_tab is None:
            address = _get_address_bar_text(win)
            if address and pattern.search(address) and len(tab_items) == 1:
                matched_tab = tab_items[0]

        if matched_tab is not None:
            try:
                win.SetActive()
                time.sleep(0.2)
                matched_tab.Click(simulateMove=False)
                time.sleep(0.2)
                auto.SendKeys("{Ctrl}w")
            except Exception as exc:
                logger.exception("UIA click/close failed on tab matching '%s'", query)
                return CapabilityResult.fail(
                    "close_browser_tab", f"Found the tab but failed to close it: {exc}"
                )
            return CapabilityResult.ok(
                "close_browser_tab", {"closed_tab_matching": query, "browser": browser or "chromium"}
            )

    return CapabilityResult.fail(
        "close_browser_tab",
        f"No open tab matching '{query}' was found in {browser or 'Chrome/Edge'}. "
        f"Open tab titles were: {seen_titles or '[]'}.",
    )


registry.register(
    name="close_browser_tab",
    function=close_browser_tab,
    description=(
        "Close one specific browser tab by matching part of its title or "
        "URL (e.g. 'pop.com'), leaving all other tabs and windows open. "
        "Works for Chromium-based browsers (Chrome, Edge, Brave). Optionally "
        "specify browser as 'chrome' or 'edge' to target only that browser."
    ),
    parameters={
        "type": "object",
        "properties": {
            "title_query": {"type": "string", "description": "Text to match in the tab's title or URL."},
            "browser": {"type": "string", "description": "Optional: 'chrome' or 'edge' to restrict the search."},
        },
        "required": ["title_query"],
    },
    risk="safe",
)
