"""
Browser capabilities: opening URLs with real control over tab vs window,
listing what's open, closing a specific tab without touching others, and
playing a YouTube video directly (not just opening a search page).

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

play_youtube resolves a search query to a real video ID by scraping
YouTube's search results HTML for the first "videoId" occurrence (no API
key required), then opens that video's watch URL with autoplay=1 — this
is what makes it actually PLAY something instead of just opening a
search results listing.

Window activation uses AttachThreadInput rather than plain
SetForegroundWindow/SwitchToThisWindow, because Windows' focus-stealing
prevention silently denies foreground requests from background processes
(visible as a blinking taskbar icon while the target window never
actually comes forward). AttachThreadInput temporarily borrows the
currently-foreground thread's input permissions so our activation
request is treated as if it came from the already-focused app, which
Windows allows.
"""

from __future__ import annotations

import ctypes
import logging
import os
import re
import subprocess
import time
import urllib.parse
import urllib.request

import uiautomation as auto

try:
    ctypes.windll.shcore.SetProcessDpiAwareness(2)  # PROCESS_PER_MONITOR_DPI_AWARE
except Exception:
    try:
        ctypes.windll.user32.SetProcessDPIAware()  # fallback for older Windows
    except Exception:
        pass

from core.app_paths import resolve_executable
from core.registry import registry
from core.result import CapabilityResult
from core.window_focus import force_activate_window

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
        "this for local folders/files — use open_folder instead. Do NOT "
        "use this to play a song/video on YouTube — use play_youtube "
        "instead, since this only opens a page/search results, it never "
        "plays anything."
    ),
    parameters={
        "type": "object",
        "properties": {
            "url": {"type": "string", "description": "URL or site to open, e.g. 'google.com'."},
            "browser": {
                "type": ["string", "null"],
                "description": "Specific browser to use: 'chrome' or 'edge'. Omit or pass null if unspecified.",
            },
            "new_window": {
                "type": ["boolean", "null"],
                "description": "Force a new separate window instead of a new tab. Defaults to false; pass null if unspecified.",
            },
        },
        "required": ["url"],
    },
    risk="safe",
)


# ---------------------------------------------------------------------------
# YouTube: resolve a query to a real video and play it, not just search.
# ---------------------------------------------------------------------------

_YOUTUBE_SEARCH_URL = "https://www.youtube.com/results"
_YOUTUBE_WATCH_URL = "https://www.youtube.com/watch"


def _fetch_first_youtube_video_id(query: str) -> str | None:
    """Resolve a query to a video ID via the YouTube Data API v3 search
    endpoint. This returns a tiny JSON response (a few KB) in well under
    a second, unlike scraping the full HTML search page — which is
    multi-hundred-KB, chunked, and can take 20-30+ seconds depending on
    network conditions."""
    api_key = os.environ.get("YOUTUBE_API_KEY")
    if not api_key:
        logger.warning("YOUTUBE_API_KEY not set; falling back to slow HTML scrape.")
        return _fetch_first_youtube_video_id_via_scrape(query)

    params = urllib.parse.urlencode({
        "part": "snippet",
        "type": "video",
        "maxResults": 1,
        "q": query,
        "key": api_key,
    })
    url = f"https://www.googleapis.com/youtube/v3/search?{params}"
    req = urllib.request.Request(url, headers={"Accept": "application/json"})

    try:
        with urllib.request.urlopen(req, timeout=5) as resp:
            import json
            data = json.loads(resp.read().decode("utf-8"))
    except Exception:
        logger.exception("YouTube Data API search failed for '%s'; falling back to scrape.", query)
        return _fetch_first_youtube_video_id_via_scrape(query)

    items = data.get("items") or []
    if not items:
        return None
    return items[0].get("id", {}).get("videoId")


def _fetch_first_youtube_video_id_via_scrape(query: str) -> str | None:
    """Fallback only: scrape YouTube's search results HTML for the first
    video ID when no API key is configured or the API call fails. This
    path is slower and less reliable — prefer setting YOUTUBE_API_KEY."""
    params = urllib.parse.urlencode({"search_query": query})
    url = f"{_YOUTUBE_SEARCH_URL}?{params}"
    req = urllib.request.Request(
        url,
        headers={"User-Agent": "Mozilla/5.0", "Accept-Encoding": "gzip"},
    )

    pattern = re.compile(rb'"videoId":"([A-Za-z0-9_-]{11})"')
    max_bytes = 1_000_000
    chunk_size = 65_536

    try:
        with urllib.request.urlopen(req, timeout=6) as resp:
            import gzip
            raw = b""
            total = 0
            while total < max_bytes:
                chunk = resp.read(chunk_size)
                if not chunk:
                    break
                raw += chunk
                total += len(chunk)
                if resp.headers.get("Content-Encoding") == "gzip":
                    try:
                        decompressed = gzip.decompress(raw)
                    except OSError:
                        continue  # not enough data yet to decompress fully
                else:
                    decompressed = raw
                match = pattern.search(decompressed)
                if match:
                    return match.group(1).decode()
    except Exception:
        logger.exception("Fallback scrape failed for '%s'", query)
        return None

    return None


def play_youtube(query: str, browser: str | None = None, new_window: bool = False) -> CapabilityResult:
    t0 = time.monotonic()
    query = query.strip()
    if not query:
        return CapabilityResult.fail("play_youtube", "No song/video name was given to search for.")

    video_id = _fetch_first_youtube_video_id(query)
    t1 = time.monotonic()
    logger.info("play_youtube: fetch took %.2fs, video_id=%s", t1 - t0, video_id)

    if video_id is None:
        search_url = f"{_YOUTUBE_SEARCH_URL}?{urllib.parse.urlencode({'search_query': query})}"
        return open_url(search_url, browser=browser, new_window=new_window)

    watch_url = f"{_YOUTUBE_WATCH_URL}?{urllib.parse.urlencode({'v': video_id, 'autoplay': '1'})}"
    result = open_url(watch_url, browser=browser, new_window=new_window)
    t2 = time.monotonic()
    logger.info("play_youtube: open_url took %.2fs", t2 - t1)
    return result


registry.register(
    name="play_youtube",
    function=play_youtube,
    description=(
        "Search YouTube for a song, video, or artist and PLAY the top "
        "matching video directly by opening its watch page with autoplay — "
        "not just a search results listing. Use this whenever the user "
        "asks to 'play <song/video> on YouTube' or 'play <artist> on "
        "YouTube'. Do not use open_url for this."
    ),
    parameters={
        "type": "object",
        "properties": {
            "query": {"type": "string", "description": "Song, video, or artist name to search for and play."},
            "browser": {
                "type": ["string", "null"],
                "description": "Specific browser to use: 'chrome' or 'edge'. Omit or pass null if unspecified.",
            },
            "new_window": {
                "type": ["boolean", "null"],
                "description": "Force a new separate window instead of a new tab. Defaults to false; pass null if unspecified.",
            },
        },
        "required": ["query"],
    },
    risk="safe",
)


# ---------------------------------------------------------------------------
# Window activation & tab-level inspection via UI Automation.
# ---------------------------------------------------------------------------

def _force_activate_window(win: auto.Control) -> bool:
    """Thin wrapper over core.window_focus so this fix lives in exactly
    one place, shared with capabilities/windows.py."""
    return force_activate_window(win.NativeWindowHandle)


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

def _pick_primary_window(windows: list[auto.Control]) -> auto.Control:
    """Prefer whichever discovered Chromium window is currently the
    foreground window (e.g. the one open_application just activated, or
    whichever the user was last interacting with), falling back to the
    first discovered window only if none match. This guards against
    acting on the wrong window in the rare case where more than one
    genuine Chrome/Edge window is open at once."""
    try:
        fg_hwnd = ctypes.windll.user32.GetForegroundWindow()
        for w in windows:
            if w.NativeWindowHandle == fg_hwnd:
                return w
    except Exception:
        pass
    return windows[0]

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
                if not _force_activate_window(win):
                    return CapabilityResult.fail(
                        "close_browser_tab", "Could not bring the browser window to the foreground."
                    )
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

# ---------------------------------------------------------------------------
# Active-tab control: switch to, navigate, scroll, reopen — without
# closing or disturbing any other tabs/windows.
# ---------------------------------------------------------------------------

def switch_to_tab(title_query: str, browser: str | None = None) -> CapabilityResult:
    """Bring a specific already-open tab to the foreground (by title or URL
    match) without closing anything. Use this instead of open_url when the
    user wants to go back to a tab that's already open."""
    query = title_query.strip()
    if not query:
        return CapabilityResult.fail("switch_to_tab", "No tab title/text to match was provided.")

    _ensure_com_initialized()

    try:
        windows = _find_chromium_windows(browser)
    except Exception as exc:
        logger.exception("UIA window enumeration failed")
        return CapabilityResult.fail("switch_to_tab", f"Could not scan browser windows: {exc}")

    if not windows:
        return CapabilityResult.fail(
            "switch_to_tab", f"No open {browser or 'Chrome/Edge'} window was found."
        )

    pattern = re.compile(re.escape(query), re.IGNORECASE)
    seen_titles: list[str] = []

    for win in windows:
        tab_items = _collect_tab_items(win)
        seen_titles.extend(t.Name for t in tab_items if t.Name)

        matched_tab = next((t for t in tab_items if t.Name and pattern.search(t.Name)), None)
        if matched_tab is None:
            address = _get_address_bar_text(win)
            if address and pattern.search(address) and len(tab_items) == 1:
                matched_tab = tab_items[0]

        if matched_tab is not None:
            try:
                if not _force_activate_window(win):
                    return CapabilityResult.fail(
                        "switch_to_tab", "Could not bring the browser window to the foreground."
                    )
                matched_tab.Click(simulateMove=False)
            except Exception as exc:
                logger.exception("UIA click failed on tab matching '%s'", query)
                return CapabilityResult.fail("switch_to_tab", f"Found the tab but failed to switch to it: {exc}")
            return CapabilityResult.ok(
                "switch_to_tab", {"switched_to_tab_matching": query, "browser": browser or "chromium"}
            )

    return CapabilityResult.fail(
        "switch_to_tab",
        f"No open tab matching '{query}' was found in {browser or 'Chrome/Edge'}. "
        f"Open tab titles were: {seen_titles or '[]'}.",
    )


registry.register(
    name="switch_to_tab",
    function=switch_to_tab,
    description=(
        "Bring an already-open browser tab to the foreground by matching "
        "part of its title or URL, without closing or opening anything. "
        "Use this when the user wants to go back to a tab that's already "
        "open, instead of open_url (which may open a duplicate)."
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

def switch_tab_direction(direction: str, browser: str | None = None) -> CapabilityResult:
    """Cycle the browser's active tab to the next/previous tab (like
    pressing Ctrl+Tab), after force-activating that browser window
    FIRST — so the keystroke reliably lands on the browser, not
    whatever window currently has OS-level focus (e.g. VS Code)."""
    direction = direction.strip().lower()
    if direction not in ("next", "previous", "right", "left"):
        return CapabilityResult.fail(
            "switch_tab_direction", "direction must be 'next'/'right' or 'previous'/'left'."
        )

    _ensure_com_initialized()

    try:
        windows = _find_chromium_windows(browser)
    except Exception as exc:
        logger.exception("UIA window enumeration failed")
        return CapabilityResult.fail("switch_tab_direction", f"Could not scan browser windows: {exc}")

    if not windows:
        return CapabilityResult.fail(
            "switch_tab_direction", f"No open {browser or 'Chrome/Edge'} window was found."
        )

    win = _pick_primary_window(windows)
    try:
        if not _force_activate_window(win):
            return CapabilityResult.fail(
                "switch_tab_direction", "Could not bring the browser window to the foreground."
            )
        if direction in ("next", "right"):
            auto.SendKeys("{Ctrl}{Tab}")
        else:
            auto.SendKeys("{Ctrl}{Shift}{Tab}")
    except Exception as exc:
        logger.exception("Failed to switch tab direction")
        return CapabilityResult.fail("switch_tab_direction", f"Failed to switch tab: {exc}")

    return CapabilityResult.ok(
        "switch_tab_direction", {"direction": direction, "browser": browser or "chromium"}
    )


registry.register(
    name="switch_tab_direction",
    function=switch_tab_direction,
    description=(
        "Cycle the browser's ACTIVE tab to the next ('next' or 'right') or "
        "previous ('previous' or 'left') tab within a browser window, like "
        "pressing Ctrl+Tab. This ALWAYS force-activates the target browser "
        "window first. Use this instead of the generic hotkey tool "
        "whenever the user wants to switch/cycle a browser tab — hotkey "
        "would send Ctrl+Tab to whatever window the OS currently has "
        "focused, which may not be the browser at all."
    ),
    parameters={
        "type": "object",
        "properties": {
            "direction": {
                "type": "string",
                "enum": ["next", "previous", "right", "left"],
                "description": "Direction to cycle the active tab.",
            },
            "browser": {"type": "string", "description": "Optional: 'chrome' or 'edge' to restrict the search."},
        },
        "required": ["direction"],
    },
    risk="safe",
)


def navigate_active_tab(url: str, browser: str | None = None) -> CapabilityResult:
    """Navigate the currently active tab to a new URL, replacing its
    current page, instead of opening a new tab."""
    url = url.strip()
    if not url:
        return CapabilityResult.fail("navigate_active_tab", "No URL provided.")
    target = url if url.startswith(("http://", "https://")) else f"https://{url}"

    _ensure_com_initialized()

    try:
        windows = _find_chromium_windows(browser)
    except Exception as exc:
        logger.exception("UIA window enumeration failed")
        return CapabilityResult.fail("navigate_active_tab", f"Could not scan browser windows: {exc}")

    if not windows:
        return CapabilityResult.fail(
            "navigate_active_tab", f"No open {browser or 'Chrome/Edge'} window was found."
        )

    win = windows[0]
    try:
        if not _force_activate_window(win):
            return CapabilityResult.fail(
                "navigate_active_tab", "Could not bring the browser window to the foreground."
            )
        edit = win.EditControl(searchDepth=12)
        if not edit.Exists(1, 0.3):
            return CapabilityResult.fail("navigate_active_tab", "Could not locate the address bar.")
        pattern = edit.GetValuePattern()
        if pattern is None:
            return CapabilityResult.fail("navigate_active_tab", "Address bar does not support text input.")
        pattern.SetValue(target)
        auto.SendKeys("{Enter}")
    except Exception as exc:
        logger.exception("Failed to navigate active tab")
        return CapabilityResult.fail("navigate_active_tab", f"Failed to navigate: {exc}")

    return CapabilityResult.ok("navigate_active_tab", {"url": target, "browser": browser or "chromium"})


registry.register(
    name="navigate_active_tab",
    function=navigate_active_tab,
    description=(
        "Navigate the CURRENTLY ACTIVE tab to a new URL, replacing its "
        "page in place. Use this when the user wants to 'go to X' in the "
        "tab that's already open/focused, instead of opening a new one."
    ),
    parameters={
        "type": "object",
        "properties": {
            "url": {"type": "string", "description": "URL or site to navigate to."},
            "browser": {"type": "string", "description": "Optional: 'chrome' or 'edge' to restrict the search."},
        },
        "required": ["url"],
    },
    risk="safe",
)


def scroll_active_tab(direction: str = "down", amount: int = 3, browser: str | None = None) -> CapabilityResult:
    """Scroll the currently active tab up or down using simulated mouse-wheel
    input (targets whatever is under the cursor, not keyboard focus — so it
    works even if focus is stuck on the address bar/tab strip)."""
    direction = direction.strip().lower()
    if direction not in ("up", "down"):
        return CapabilityResult.fail("scroll_active_tab", "direction must be 'up' or 'down'.")

    _ensure_com_initialized()

    try:
        windows = _find_chromium_windows(browser)
    except Exception as exc:
        logger.exception("UIA window enumeration failed")
        return CapabilityResult.fail("scroll_active_tab", f"Could not scan browser windows: {exc}")

    if not windows:
        return CapabilityResult.fail(
            "scroll_active_tab", f"No open {browser or 'Chrome/Edge'} window was found."
        )

    win = windows[0]
    try:
        if not _force_activate_window(win):
            return CapabilityResult.fail(
                "scroll_active_tab", "Could not bring the Chrome window to the foreground."
            )

        # Guard against a cold-start UIA cache glitch: the very first
        # BoundingRectangle read after startup can return a stale/degenerate
        # rect (e.g. all zeros) before the live query populates. Retry a
        # couple of times with a fresh window lookup if that happens.
        rect = win.BoundingRectangle
        attempts = 0
        while (rect.right <= rect.left or rect.bottom <= rect.top) and attempts < 3:
            time.sleep(0.15)
            windows = _find_chromium_windows(browser)
            if not windows:
                return CapabilityResult.fail(
                    "scroll_active_tab", f"No open {browser or 'Chrome/Edge'} window was found."
                )
            win = _pick_primary_window(windows)
            rect = win.BoundingRectangle
            attempts += 1

        if rect.right <= rect.left or rect.bottom <= rect.top:
            return CapabilityResult.fail(
                "scroll_active_tab", "Could not determine the browser window's position on screen."
            )

        x = (rect.left + rect.right) // 2
        y = rect.top + 220

        auto.SetCursorPos(x, y)
        time.sleep(0.1)

        if direction == "down":
            auto.WheelDown(wheelTimes=amount)
        else:
            auto.WheelUp(wheelTimes=amount)
    except Exception as exc:
        logger.exception("Failed to scroll active tab")
        return CapabilityResult.fail("scroll_active_tab", f"Failed to scroll: {exc}")

    return CapabilityResult.ok("scroll_active_tab", {"direction": direction, "amount": amount})


registry.register(
    name="scroll_active_tab",
    function=scroll_active_tab,
    description="Scroll the currently active browser tab up or down.",
    parameters={
        "type": "object",
        "properties": {
            "direction": {"type": "string", "enum": ["up", "down"], "description": "Scroll direction."},
            "amount": {"type": "integer", "description": "Number of scroll steps. Defaults to 3."},
            "browser": {"type": "string", "description": "Optional: 'chrome' or 'edge' to restrict the search."},
        },
        "required": ["direction"],
    },
    risk="safe",
)


def reopen_closed_tab(browser: str | None = None) -> CapabilityResult:
    """Reopen the most recently closed tab (undo a close), in a given/any
    running Chromium browser window."""
    _ensure_com_initialized()

    try:
        windows = _find_chromium_windows(browser)
    except Exception as exc:
        logger.exception("UIA window enumeration failed")
        return CapabilityResult.fail("reopen_closed_tab", f"Could not scan browser windows: {exc}")

    if not windows:
        return CapabilityResult.fail(
            "reopen_closed_tab", f"No open {browser or 'Chrome/Edge'} window was found."
        )

    try:
        target_window = _pick_primary_window(windows)
        if not _force_activate_window(target_window):
            return CapabilityResult.fail(
                "reopen_closed_tab", "Could not bring the browser window to the foreground."
            )
        auto.SendKeys("{Ctrl}{Shift}t")
    except Exception as exc:
        logger.exception("Failed to reopen closed tab")
        return CapabilityResult.fail("reopen_closed_tab", f"Failed to reopen tab: {exc}")

    return CapabilityResult.ok("reopen_closed_tab", {"browser": browser or "chromium"})


registry.register(
    name="reopen_closed_tab",
    function=reopen_closed_tab,
    description="Reopen the most recently closed browser tab (undo a close).",
    parameters={
        "type": "object",
        "properties": {
            "browser": {"type": "string", "description": "Optional: 'chrome' or 'edge' to restrict the search."},
        },
        "required": [],
    },
    risk="safe",
)
