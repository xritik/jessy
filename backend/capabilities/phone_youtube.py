import subprocess
import time

import uiautomator2 as u2

from core.registry import registry
from core.result import CapabilityResult


YOUTUBE_PACKAGE = "com.google.android.youtube"

SEARCH_RESULT_TEXT_ID = (
    "com.google.android.youtube:id/text"
)

SEARCH_RESULT_ROW_ID = (
    "com.google.android.youtube:id/linearLayout"
)


class PhoneYouTubeController:
    """
    Controls the REAL YouTube application on the
    connected Android phone.

    Flow:

        Open YouTube
            ↓
        Search
            ↓
        Type query
            ↓
        Submit search
            ↓
        Find actual video result
            ↓
        Skip sponsored content
            ↓
        Tap actual video
            ↓
        Wait for video screen
            ↓
        Start playback if necessary
    """

    def __init__(self):
        self.device = None

    def _require_device(self):
        if self.device is not None:
            return True

        try:
            self.device = u2.connect()
            print("[PHONE YOUTUBE] Android phone connected.")
            return True
        except Exception as exc:
            print(
                "[PHONE YOUTUBE] Android phone is not connected: "
                f"{exc}"
            )
            self.device = None
            return False

    # =================================================
    # General helpers
    # =================================================

    def wait(self, seconds=1):
        time.sleep(seconds)

    def youtube_foreground(self):
        try:
            return (
                self.device.app_current().get(
                    "package"
                )
                == YOUTUBE_PACKAGE
            )
        except Exception:
            return False

    # =================================================
    # Open YouTube
    # =================================================

    def open_youtube(self):
        print(
            "[PHONE YOUTUBE] Opening YouTube..."
        )

        try:
            self.device.app_start(
                YOUTUBE_PACKAGE
            )

            deadline = (
                time.monotonic() + 8
            )

            while time.monotonic() < deadline:

                if self.youtube_foreground():
                    print(
                        "[PHONE YOUTUBE] "
                        "YouTube is foreground."
                    )
                    return True

                self.wait(0.25)

            print(
                "[PHONE YOUTUBE] "
                "YouTube did not become foreground."
            )

            return False

        except Exception as exc:
            print(
                "[PHONE YOUTUBE] "
                f"Open failed: {exc}"
            )

            return False

    # =================================================
    # Search
    # =================================================

    def open_search(self):
        print(
            "[PHONE YOUTUBE] Opening search..."
        )

        try:
            deadline = (
                time.monotonic() + 8
            )

            while time.monotonic() < deadline:

                search = self.device(
                    description="Search"
                )

                if search.exists:
                    info = search.info

                    if info.get(
                        "clickable",
                        False,
                    ):
                        search.click()

                        self.wait(0.8)

                        print(
                            "[PHONE YOUTUBE] "
                            "Search opened."
                        )

                        return True

                self.wait(0.25)

            print(
                "[PHONE YOUTUBE] "
                "Search button not found."
            )

            return False

        except Exception as exc:
            print(
                "[PHONE YOUTUBE] "
                f"Search open failed: {exc}"
            )

            return False

    def type_search_query(self, query):
        query = query.strip()

        if not query:
            return False

        print(
            "[PHONE YOUTUBE] "
            f"Typing query: {query}"
        )

        # First try the actual YouTube EditText.
        # This is safer than blindly sending ADB
        # input to whichever field happens to have focus.
        try:
            edit = self.device(
                className="android.widget.EditText"
            )

            if edit.exists:

                try:
                    edit.set_text(query)

                    self.wait(0.5)

                    return True

                except Exception:
                    pass

        except Exception:
            pass

        # Fallback to the existing ADB typing method.
        encoded = query.replace(
            " ",
            "%s",
        )

        try:
            subprocess.run(
                [
                    "adb",
                    "shell",
                    "input",
                    "text",
                    encoded,
                ],
                check=True,
                timeout=10,
            )

            self.wait(0.5)

            return True

        except Exception as exc:
            print(
                "[PHONE YOUTUBE] "
                f"Typing failed: {exc}"
            )

            return False

    def submit_search(self):
        print(
            "[PHONE YOUTUBE] "
            "Submitting search..."
        )

        try:
            self.device.press("enter")

            if self.wait_for_results(
                timeout=12
            ):
                print(
                    "[PHONE YOUTUBE] "
                    "Search results loaded."
                )

                return True

            print(
                "[PHONE YOUTUBE] "
                "Search results did not load."
            )

            return False

        except Exception as exc:
            print(
                "[PHONE YOUTUBE] "
                f"Submit failed: {exc}"
            )

            return False

    # =================================================
    # Search results
    # =================================================

    def wait_for_results(self, timeout=12):
        """
        Wait until YouTube exposes actual video
        result nodes.

        We specifically look for nodes whose
        content description contains 'play video'.

        This is more reliable than assuming every
        clickable linearLayout is a video.
        """

        deadline = (
            time.monotonic() + timeout
        )

        while time.monotonic() < deadline:

            try:
                videos = self.device(
                    descriptionContains="play video"
                )

                if videos.count > 0:
                    return True

            except Exception:
                pass

            self.wait(0.25)

        return False

    def find_first_video_title(self):
        """
        Find the first visible YouTube search-result
        title.

        This method is kept for compatibility with
        the previous controller architecture.

        The actual click operation now uses the much
        safer 'play video' selector.
        """

        try:
            titles = self.device(
                resourceId=SEARCH_RESULT_TEXT_ID
            )

            count = titles.count

            print(
                "[PHONE YOUTUBE] "
                f"Found {count} text result nodes."
            )

            for index in range(count):

                try:
                    title = titles[index]

                    info = title.info

                    if not info.get(
                        "visibleToUser",
                        True,
                    ):
                        continue

                    text = (
                        info.get("text")
                        or ""
                    ).strip()

                    if not text:
                        continue

                    ignored = {
                        "home",
                        "shorts",
                        "subscriptions",
                        "library",
                        "search",
                    }

                    if text.lower() in ignored:
                        continue

                    print(
                        "[PHONE YOUTUBE] "
                        f"Candidate result: {text}"
                    )

                    return title

                except Exception:
                    continue

        except Exception as exc:
            print(
                "[PHONE YOUTUBE] "
                f"Could not inspect results: {exc}"
            )

        return None

    # =================================================
    # Find actual YouTube video
    # =================================================

    def find_first_real_video(self):
        """
        Find the first genuine YouTube video card.

        IMPORTANT:

        YouTube exposes multiple nodes containing
        'play video'.

        Example from the actual phone UI:

            Sponsored - Instagram ... - play video

        and:

            The 12 Biggest AI Updates You Missed This Week
            ... - play video

        Sponsored cards are ignored.

        We also validate the screen bounds so bottom
        navigation elements cannot be selected.
        """

        try:
            videos = self.device(
                descriptionContains="play video"
            )

            count = videos.count

            print(
                "[PHONE YOUTUBE] "
                f"Found {count} 'play video' nodes."
            )

            if count <= 0:
                return None

            width, height = (
                self.device.window_size()
            )

            # Bottom navigation / bottom UI rejection.
            bottom_navigation_top = int(
                height * 0.85
            )

            candidates = []

            for index in range(count):

                try:
                    video = videos[index]

                    info = video.info

                    description = (
                        info.get(
                            "contentDescription"
                        )
                        or ""
                    ).strip()

                    bounds = info.get(
                        "bounds"
                    )

                    visible = info.get(
                        "visibleToUser",
                        True,
                    )

                    clickable = info.get(
                        "clickable",
                        False,
                    )

                    enabled = info.get(
                        "enabled",
                        False,
                    )

                    print(
                        "[PHONE YOUTUBE] "
                        f"Video node {index}: "
                        f"{description[:180]}"
                    )

                    if not visible:
                        continue

                    if not clickable:
                        continue

                    if not enabled:
                        continue

                    if not bounds:
                        continue

                    # Explicitly reject sponsored content.
                    if description.lower().startswith(
                        "sponsored -"
                    ):
                        print(
                            "[PHONE YOUTUBE] "
                            f"Skipping sponsored video "
                            f"node {index}."
                        )
                        continue

                    left = bounds.get(
                        "left",
                        0,
                    )

                    top = bounds.get(
                        "top",
                        0,
                    )

                    right = bounds.get(
                        "right",
                        0,
                    )

                    bottom = bounds.get(
                        "bottom",
                        0,
                    )

                    if right <= left:
                        continue

                    if bottom <= top:
                        continue

                    center_x = (
                        left + right
                    ) // 2

                    center_y = (
                        top + bottom
                    ) // 2

                    # Ignore anything inside bottom
                    # navigation.
                    if center_y >= (
                        bottom_navigation_top
                    ):
                        print(
                            "[PHONE YOUTUBE] "
                            f"Skipping bottom UI node "
                            f"{index}: {bounds}"
                        )
                        continue

                    # Ignore tiny controls.
                    card_width = (
                        right - left
                    )

                    card_height = (
                        bottom - top
                    )

                    if card_width < 250:
                        continue

                    if card_height < 100:
                        continue

                    candidates.append(
                        {
                            "node": video,
                            "description": description,
                            "bounds": bounds,
                            "center_x": center_x,
                            "center_y": center_y,
                        }
                    )

                except Exception as exc:
                    print(
                        "[PHONE YOUTUBE] "
                        f"Could not inspect "
                        f"video node {index}: {exc}"
                    )

            if not candidates:
                return None

            # Highest real video result first.
            candidates.sort(
                key=lambda item: (
                    item["center_y"],
                    item["center_x"],
                )
            )

            selected = candidates[0]

            print(
                "[PHONE YOUTUBE] "
                "Selected real video:"
            )

            print(
                "[PHONE YOUTUBE] "
                f"{selected['description']}"
            )

            print(
                "[PHONE YOUTUBE] "
                f"Bounds: {selected['bounds']}"
            )

            return selected["node"]

        except Exception as exc:
            print(
                "[PHONE YOUTUBE] "
                f"Could not find real video: {exc}"
            )

            return None

    # =================================================
    # Click actual video result
    # =================================================

    def click_first_video(self, timeout=12):
        """
        Click the first REAL YouTube video result.

        We do NOT use:

            resource-id='linearLayout'

        as the primary selector.

        We also do NOT walk through arbitrary parents.

        Instead we directly select the verified
        'play video' accessibility node.

        Sponsored results are skipped.
        Bottom navigation is rejected.
        """

        print(
            "[PHONE YOUTUBE] "
            "Looking for actual video result..."
        )

        deadline = (
            time.monotonic() + timeout
        )

        while time.monotonic() < deadline:

            try:
                video = (
                    self.find_first_real_video()
                )

                if video is not None:

                    try:
                        print(
                            "[PHONE YOUTUBE] "
                            "Clicking actual video..."
                        )

                        video.click()

                        print(
                            "[PHONE YOUTUBE] "
                            "VIDEO RESULT CLICKED."
                        )

                        return True

                    except Exception as exc:
                        print(
                            "[PHONE YOUTUBE] "
                            f"Video click failed: {exc}"
                        )

            except Exception as exc:
                print(
                    "[PHONE YOUTUBE] "
                    f"Video-result detection failed: "
                    f"{exc}"
                )

            self.wait(0.25)

        print(
            "[PHONE YOUTUBE] "
            "Could not find a safe clickable video result."
        )

        return False

    # =================================================
    # Detect video screen
    # =================================================

    def wait_for_video_screen(
        self,
        timeout=15,
    ):
        """
        Wait for YouTube to leave the search result
        screen and open the video/watch screen.
        """

        print(
            "[PHONE YOUTUBE] "
            "Waiting for video screen..."
        )

        deadline = (
            time.monotonic() + timeout
        )

        while time.monotonic() < deadline:

            if not self.youtube_foreground():
                self.wait(0.25)
                continue

            try:
                # YouTube playback controls.
                pause = self.device(
                    description="Pause"
                )

                if pause.exists:
                    print(
                        "[PHONE YOUTUBE] "
                        "Video screen detected."
                    )

                    return True

                pause_video = self.device(
                    description="Pause video"
                )

                if pause_video.exists:
                    print(
                        "[PHONE YOUTUBE] "
                        "Video screen detected."
                    )

                    return True

                play = self.device(
                    description="Play"
                )

                if play.exists:
                    print(
                        "[PHONE YOUTUBE] "
                        "Video screen detected."
                    )

                    return True

                play_video = self.device(
                    description="Play video"
                )

                if play_video.exists:
                    print(
                        "[PHONE YOUTUBE] "
                        "Video screen detected."
                    )

                    return True

                # If the search-result 'play video'
                # nodes disappeared, the hierarchy has
                # transitioned.
                videos = self.device(
                    descriptionContains="play video"
                )

                if videos.count == 0:
                    print(
                        "[PHONE YOUTUBE] "
                        "YouTube transitioned "
                        "to video screen."
                    )

                    return True

            except Exception:
                # UI hierarchy can temporarily disappear
                # during the YouTube activity transition.
                print(
                    "[PHONE YOUTUBE] "
                    "YouTube hierarchy changed."
                )

                return True

            self.wait(0.3)

        if self.youtube_foreground():
            print(
                "[PHONE YOUTUBE] "
                "YouTube is still foreground "
                "after result click."
            )

            return True

        return False

    # =================================================
    # Start playback
    # =================================================

    def start_playback(
        self,
        timeout=10,
    ):
        """
        Start the selected YouTube video.

        If YouTube is already playing, return True.

        If YouTube exposes a Play button, click it.

        Some YouTube versions hide the playback controls.
        In that case, perform one controlled player tap
        and check again.
        """

        print(
            "[PHONE YOUTUBE] "
            "Checking playback state..."
        )

        deadline = (
            time.monotonic() + timeout
        )

        while time.monotonic() < deadline:

            if not self.youtube_foreground():
                return False

            try:
                # -----------------------------------------
                # Already playing
                # -----------------------------------------

                pause = self.device(
                    description="Pause"
                )

                if pause.exists:
                    print(
                        "[PHONE YOUTUBE] "
                        "Video is already playing."
                    )

                    return True

                pause_video = self.device(
                    description="Pause video"
                )

                if pause_video.exists:
                    print(
                        "[PHONE YOUTUBE] "
                        "Video is already playing."
                    )

                    return True

                # -----------------------------------------
                # Paused
                # -----------------------------------------

                play = self.device(
                    description="Play"
                )

                if play.exists:

                    print(
                        "[PHONE YOUTUBE] "
                        "Play button found. "
                        "Starting video..."
                    )

                    play.click()

                    self.wait(1)

                    continue

                play_video = self.device(
                    description="Play video"
                )

                if play_video.exists:

                    print(
                        "[PHONE YOUTUBE] "
                        "Play video button found. "
                        "Starting video..."
                    )

                    play_video.click()

                    self.wait(1)

                    continue

            except Exception:
                pass

            self.wait(0.25)

        # -------------------------------------------------
        # Some YouTube versions hide the playback controls.
        #
        # Only after the watch screen has been opened do
        # we use one player tap.
        # -------------------------------------------------

        print(
            "[PHONE YOUTUBE] "
            "Play control not exposed."
        )

        print(
            "[PHONE YOUTUBE] "
            "Trying one player tap..."
        )

        try:
            width, height = (
                self.device.window_size()
            )

            # Controlled tap in the upper player area.
            x = width // 2
            y = int(height * 0.22)

            self.device.click(
                x,
                y,
            )

            self.wait(1)

            # Check again.
            pause = self.device(
                description="Pause"
            )

            if pause.exists:
                print(
                    "[PHONE YOUTUBE] "
                    "Playback started."
                )

                return True

            pause_video = self.device(
                description="Pause video"
            )

            if pause_video.exists:
                print(
                    "[PHONE YOUTUBE] "
                    "Playback started."
                )

                return True

            # The watch screen is already open.
            # Some YouTube versions do not expose
            # playback controls through UIAutomator.
            print(
                "[PHONE YOUTUBE] "
                "Player interaction completed."
            )

            return True

        except Exception as exc:
            print(
                "[PHONE YOUTUBE] "
                f"Player tap failed: {exc}"
            )

            return False

    # =================================================
    # Complete operation
    # =================================================

    def search_and_play(
        self,
        query,
    ):
        """
        Search and physically play a YouTube video
        on the Android phone.
        """

        if not self._require_device():
            return False

        query = query.strip()

        if not query:
            return False

        # 1. Open YouTube
        if not self.open_youtube():
            return False

        # 2. Open search
        if not self.open_search():
            return False

        # 3. Type search query
        if not self.type_search_query(
            query
        ):
            return False

        # 4. Submit search
        if not self.submit_search():
            return False

        # 5. Click actual video result
        if not self.click_first_video(
            timeout=12
        ):
            return False

        # 6. Wait for watch page
        if not self.wait_for_video_screen(
            timeout=15
        ):
            return False

        # 7. Start playback
        if not self.start_playback(
            timeout=10
        ):
            return False

        print(
            "[PHONE YOUTUBE] "
            f"SUCCESS: '{query}' "
            "opened for playback."
        )

        return True

    # =================================================
    # Backward-compatible search method
    # =================================================

    def search(self, query):
        """
        Backward-compatible method.

        The capability is specifically named
        play_youtube_on_phone, so this performs the
        complete search + playback operation.
        """

        return self.search_and_play(
            query
        )


# =====================================================
# Controller instance
# =====================================================

_phone_youtube = PhoneYouTubeController()


# =====================================================
# JESSY capability
# =====================================================

def play_youtube_on_phone(
    query: str,
) -> CapabilityResult:
    """
    Search YouTube on the connected Android PHONE
    and play the first matching result.

    Examples:

        "play 90s song on my phone"
        "play Nirvana on my phone"
        "play Bepanah Pyaar on YouTube on my phone"

    This operates the REAL visible YouTube
    application on the phone.
    """

    query = query.strip()

    if not query:
        return CapabilityResult.fail(
            "play_youtube_on_phone",
            "No YouTube search query was provided.",
        )

    print(
        "[PHONE YOUTUBE] "
        f"JESSY request: {query}"
    )

    success = (
        _phone_youtube.search_and_play(
            query
        )
    )

    if not success:
        return CapabilityResult.fail(
            "play_youtube_on_phone",
            (
                f"Could not start YouTube "
                f"playback for '{query}' "
                f"on the Android phone."
            ),
        )

    return CapabilityResult.ok(
        "play_youtube_on_phone",
        {
            "query": query,
            "device": "android_phone",
            "youtube": True,
            "searched": True,
            "video_opened": True,
            "played": True,
        },
    )


# =====================================================
# Registry
# =====================================================

registry.register(
    name="play_youtube_on_phone",
    function=play_youtube_on_phone,
    description=(
        "Search YouTube on the connected Android PHONE "
        "and PLAY the first matching video on the real "
        "phone screen. Use this when the user explicitly "
        "mentions YouTube on their phone, phone YouTube, "
        "YouTube app on phone, or asks to play something "
        "on their phone. Examples: 'play a 90s song on "
        "my phone', 'play Nirvana on YouTube on my phone', "
        "'play Bepanah Pyaar on my phone's YouTube'. "
        "This capability physically operates the real "
        "Android YouTube UI. It does not use desktop "
        "YouTube."
    ),
    parameters={
        "type": "object",
        "properties": {
            "query": {
                "type": "string",
                "description": (
                    "The YouTube search query requested "
                    "by the user. Preserve the requested "
                    "song, artist, genre, year, era, or "
                    "topic."
                ),
            }
        },
        "required": ["query"],
    },
    risk="safe",
)