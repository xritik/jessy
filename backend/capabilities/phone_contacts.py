import re
import time
import xml.etree.ElementTree as ET

import uiautomator2 as u2

from core.registry import registry


CONTACTS_PACKAGE = "com.google.android.contacts"

SEARCH_BAR_ID = (
    "com.google.android.contacts:id/"
    "open_search_bar"
)

SEARCH_INPUT_ID = (
    "com.google.android.contacts:id/"
    "open_search_view_edit_text"
)


class PhoneContactsController:
    """
    Controls Contacts on the connected Android phone.

    Current capability:
        - Call a contact

    Mobile-only optimization:
        - Avoid unnecessary fixed waits.
        - Poll for actual UI elements instead.
        - Detect contact results as soon as they appear.
        - No confirmation is required.
    """

    def __init__(self):
        self.device = None

    def _require_device(self):
        if self.device is not None:
            return True

        try:
            self.device = u2.connect()
            print("[PHONE CONTACTS] Android phone connected.")
            return True
        except Exception as exc:
            print(
                "[PHONE CONTACTS] Android phone is not connected: "
                f"{exc}"
            )
            self.device = None
            return False

    # =========================================================
    # BASIC HELPERS
    # =========================================================

    def current_app(self):
        return self.device.app_current()

    @staticmethod
    def wait(seconds):
        time.sleep(seconds)

    # =========================================================
    # UI WAIT HELPERS
    # =========================================================

    def _wait_for_element(
        self,
        selector,
        timeout=3.0,
        interval=0.15,
    ):
        """
        Wait until a UI element exists.

        Returns the uiautomator2 object immediately when
        it becomes available.
        """

        end_time = time.monotonic() + timeout

        while time.monotonic() < end_time:
            try:
                if selector.exists:
                    return selector
            except Exception:
                pass

            time.sleep(interval)

        return None

    def _wait_for_search_bar(self, timeout=3.0):
        return self._wait_for_element(
            self.device(
                resourceId=SEARCH_BAR_ID
            ),
            timeout=timeout,
        )

    def _wait_for_search_input(self, timeout=3.0):
        return self._wait_for_element(
            self.device(
                resourceId=SEARCH_INPUT_ID
            ),
            timeout=timeout,
        )

    def _wait_for_call_button(self, timeout=3.0):
        return self._wait_for_element(
            self.device(
                description="Call"
            ),
            timeout=timeout,
        )

    # =========================================================
    # CONTACTS APP
    # =========================================================

    def _start_contacts(self):
        """
        Start Contacts and wait only until its search bar
        becomes available.
        """

        print("Opening Contacts...")

        try:
            self.device.app_start(
                CONTACTS_PACKAGE
            )

            # Do not blindly sleep for 2 seconds.
            # Continue as soon as the UI is ready.
            search_bar = self._wait_for_search_bar(
                timeout=3.0
            )

            if search_bar is not None:
                print(
                    "Contacts search bar ready."
                )
                return True

            current = self.current_app()

            if current.get("package") != CONTACTS_PACKAGE:
                print(
                    "Contacts did not become "
                    "the foreground application."
                )

            return False

        except Exception as exc:
            print(
                f"Could not start Contacts: {exc}"
            )
            return False

    def _restart_contacts(self):
        """
        Restart Contacts only when its UI is genuinely
        unavailable.
        """

        print(
            "Contacts UI not ready. "
            "Restarting Contacts..."
        )

        try:
            self.device.app_stop(
                CONTACTS_PACKAGE
            )
        except Exception as exc:
            print(
                f"Could not stop Contacts: {exc}"
            )

        # Small pause only for process shutdown.
        self.wait(0.3)

        try:
            self.device.app_start(
                CONTACTS_PACKAGE
            )
        except Exception as exc:
            print(
                f"Could not restart Contacts: {exc}"
            )
            return False

        search_bar = self._wait_for_search_bar(
            timeout=4.0
        )

        if search_bar is None:
            print(
                "Contacts failed to become ready "
                "after restart."
            )
            return False

        print(
            "Contacts restarted successfully."
        )

        return True

    # =========================================================
    # OPEN CONTACTS
    # =========================================================

    def open_contacts(self):
        """
        Open Contacts.

        First attempt is fast.

        Contacts is restarted only if its search bar
        does not become available.
        """

        if self._start_contacts():
            return True

        print(
            "Contacts search bar not found."
        )

        return self._restart_contacts()

    # =========================================================
    # OPEN CONTACT SEARCH
    # =========================================================

    def open_contact_search(self):
        """
        Open the Contacts search interface.

        Uses UI polling instead of fixed delays.
        """

        search_bar = self._wait_for_search_bar(
            timeout=2.0
        )

        if search_bar is not None:

            try:
                search_bar.click()
            except Exception as exc:
                print(
                    f"Could not click search bar: {exc}"
                )
                return False

            search_input = self._wait_for_search_input(
                timeout=2.5
            )

            if search_input is not None:
                return True

        print(
            "Contacts search UI not ready."
        )

        # Recovery only if genuinely necessary.
        if not self._restart_contacts():
            return False

        search_bar = self._wait_for_search_bar(
            timeout=3.0
        )

        if search_bar is None:
            print(
                "Contacts search bar not found "
                "after restart."
            )
            return False

        try:
            search_bar.click()
        except Exception as exc:
            print(
                f"Could not click search bar "
                f"after restart: {exc}"
            )
            return False

        search_input = self._wait_for_search_input(
            timeout=3.0
        )

        if search_input is None:
            print(
                "Contacts search input still "
                "not found after restart."
            )
            return False

        return True

    # =========================================================
    # SEARCH CONTACT
    # =========================================================

    def search_contact(self, name):
        """
        Enter the requested contact name.

        Wait only until the actual search result
        appears instead of sleeping a fixed 2 seconds.
        """

        search_input = self._wait_for_search_input(
            timeout=2.5
        )

        if search_input is None:
            print(
                "Search input not found."
            )
            return False

        try:
            search_input.clear_text()
        except Exception:
            pass

        self.wait(0.1)

        try:
            search_input.set_text(name)
        except Exception as exc:
            print(
                f"Could not enter contact name: {exc}"
            )
            return False

        print(
            f"Contact search entered: {name}"
        )

        # Wait for the actual contact result.
        # Maximum ~3 seconds, but normally much faster.
        end_time = time.monotonic() + 3.0

        while time.monotonic() < end_time:

            result = self.find_contact_result(
                name,
                quiet=True,
            )

            if result is not None:
                return True

            time.sleep(0.15)

        # Let open_contact_result() perform one final
        # normal lookup so its existing error message
        # remains useful.
        return True

    # =========================================================
    # UI HIERARCHY HELPERS
    # =========================================================

    @staticmethod
    def _parse_bounds(raw):
        """
        Convert:

            [44,311][1036,465]

        into:

            (44, 311, 1036, 465)
        """

        match = re.fullmatch(
            r"\[(\d+),(\d+)\]\[(\d+),(\d+)\]",
            raw or "",
        )

        if not match:
            return None

        return tuple(
            map(int, match.groups())
        )

    @staticmethod
    def _contains(outer, inner):
        """
        Return True when outer completely contains inner.
        """

        (
            outer_left,
            outer_top,
            outer_right,
            outer_bottom,
        ) = outer

        (
            inner_left,
            inner_top,
            inner_right,
            inner_bottom,
        ) = inner

        return (
            outer_left <= inner_left
            and outer_top <= inner_top
            and outer_right >= inner_right
            and outer_bottom >= inner_bottom
        )

    @staticmethod
    def _normalize_contact_name(value):
        """
        Normalize a contact name.

        Examples:

            mummy       -> mummy
            Mummy❤️     -> mummy
            Mummy Ji ❤️ -> mummy ji
            Suraj 🔥    -> suraj
        """

        value = value or ""

        cleaned = "".join(
            char
            for char in value
            if char.isalnum() or char.isspace()
        )

        cleaned = " ".join(
            cleaned.split()
        )

        return cleaned.casefold()

    # =========================================================
    # FIND CONTACT RESULT
    # =========================================================

    def find_contact_result(
        self,
        name,
        quiet=False,
    ):
        """
        Find a visible contact result.

        Matching:

            1. Exact normalized match
            2. Partial normalized match

        Emojis and decorative symbols are ignored.
        """

        wanted = self._normalize_contact_name(
            name
        )

        if not quiet:
            print(
                f"Looking for contact result: {name}"
            )

        try:
            hierarchy = (
                self.device.dump_hierarchy()
            )

            root = ET.fromstring(
                hierarchy
            )

        except Exception as exc:

            if not quiet:
                print(
                    "Could not read Contacts UI "
                    f"hierarchy: {exc}"
                )

            return None

        matching_nodes = []

        # =====================================================
        # EXACT NORMALIZED MATCH
        # =====================================================

        for node in root.iter("node"):

            text = " ".join(
                (
                    node.attrib.get("text")
                    or ""
                ).split()
            )

            if not text:
                continue

            if (
                node.attrib.get("class")
                == "android.widget.EditText"
            ):
                continue

            normalized_text = (
                self._normalize_contact_name(
                    text
                )
            )

            if normalized_text != wanted:
                continue

            bounds = self._parse_bounds(
                node.attrib.get("bounds")
            )

            if not bounds:
                continue

            # Ignore toolbar/header text.
            if bounds[1] < 267:
                continue

            matching_nodes.append(
                {
                    "node": node,
                    "text": text,
                    "normalized_text": normalized_text,
                    "bounds": bounds,
                }
            )

        # =====================================================
        # PARTIAL NORMALIZED MATCH
        # =====================================================

        if not matching_nodes:

            for node in root.iter("node"):

                text = " ".join(
                    (
                        node.attrib.get("text")
                        or ""
                    ).split()
                )

                if not text:
                    continue

                if (
                    node.attrib.get("class")
                    == "android.widget.EditText"
                ):
                    continue

                normalized_text = (
                    self._normalize_contact_name(
                        text
                    )
                )

                if not normalized_text:
                    continue

                if not (
                    wanted in normalized_text
                    or normalized_text in wanted
                ):
                    continue

                bounds = self._parse_bounds(
                    node.attrib.get("bounds")
                )

                if not bounds:
                    continue

                if bounds[1] < 267:
                    continue

                matching_nodes.append(
                    {
                        "node": node,
                        "text": text,
                        "normalized_text": normalized_text,
                        "bounds": bounds,
                    }
                )

        if not matching_nodes:

            if not quiet:
                print(
                    f"Contact result not found: {name}"
                )

            return None

        # Prefer the shortest matching contact.
        matching_nodes.sort(
            key=lambda item: (
                len(item["normalized_text"]),
                item["bounds"][1],
                item["bounds"][0],
            )
        )

        result = matching_nodes[0]

        if not quiet:
            print(
                f"Contact result found: "
                f"'{result['text']}' "
                f"{result['bounds']}"
            )

        return result

    # =========================================================
    # OPEN CONTACT RESULT
    # =========================================================

    def open_contact_result(self, name):
        """
        Open the clickable row containing the
        matching contact TextView.
        """

        result = self.find_contact_result(
            name
        )

        if result is None:
            return False

        text_bounds = result["bounds"]

        try:
            hierarchy = (
                self.device.dump_hierarchy()
            )

            root = ET.fromstring(
                hierarchy
            )

        except Exception as exc:

            print(
                "Could not read UI hierarchy "
                f"while finding contact row: {exc}"
            )

            return False

        clickable_rows = []

        for node in root.iter("node"):

            if (
                node.attrib.get("clickable")
                != "true"
            ):
                continue

            if (
                node.attrib.get("visible-to-user")
                == "false"
            ):
                continue

            row_bounds = self._parse_bounds(
                node.attrib.get("bounds")
            )

            if not row_bounds:
                continue

            if not self._contains(
                row_bounds,
                text_bounds,
            ):
                continue

            left, top, right, bottom = (
                row_bounds
            )

            width = right - left
            height = bottom - top

            if width <= 0 or height <= 0:
                continue

            area = width * height

            clickable_rows.append(
                {
                    "area": area,
                    "bounds": row_bounds,
                }
            )

        if not clickable_rows:

            print(
                f"Clickable row not found for: {name}"
            )

            return False

        clickable_rows.sort(
            key=lambda item: item["area"]
        )

        target = clickable_rows[0]

        left, top, right, bottom = (
            target["bounds"]
        )

        center_x = (
            left + right
        ) // 2

        center_y = (
            top + bottom
        ) // 2

        print(
            f"Opening contact row: "
            f"[{left},{top}]"
            f"[{right},{bottom}]"
        )

        self.device.click(
            center_x,
            center_y,
        )

        return True

    # =========================================================
    # CALL BUTTON
    # =========================================================

    def has_call_button(self):
        """
        Wait until the contact detail page exposes
        its Call button.
        """

        return (
            self._wait_for_call_button(
                timeout=3.0
            )
            is not None
        )

    # =========================================================
    # DIRECT CALL
    # =========================================================

    def call(self, name):
        """
        Complete direct phone call flow.

        No confirmation is required.
        """

        if not self._require_device():
            return False

        print(
            f"Starting direct call flow for: {name}"
        )

        # -----------------------------------------------------
        # Open Contacts
        # -----------------------------------------------------

        if not self.open_contacts():
            print(
                "Could not open Contacts."
            )
            return False

        # -----------------------------------------------------
        # Open search
        # -----------------------------------------------------

        if not self.open_contact_search():
            print(
                "Could not open Contacts search."
            )
            return False

        # -----------------------------------------------------
        # Search
        # -----------------------------------------------------

        if not self.search_contact(name):
            print(
                f"Could not search for: {name}"
            )
            return False

        # -----------------------------------------------------
        # Open result
        # -----------------------------------------------------

        if not self.open_contact_result(name):
            print(
                f"Could not open contact result "
                f"for: {name}"
            )
            return False

        # -----------------------------------------------------
        # Wait for Call button
        # -----------------------------------------------------

        call_button = self._wait_for_call_button(
            timeout=3.0
        )

        if call_button is None:
            print(
                f"Call button not found for: {name}"
            )
            return False

        # -----------------------------------------------------
        # CALL
        # -----------------------------------------------------

        print(
            f"Placing call to: {name}"
        )

        try:
            call_button.click()
        except Exception as exc:
            print(
                f"Could not press Call: {exc}"
            )
            return False

        print(
            f"Call initiated: {name}"
        )

        return True


# =========================================================
# CONTROLLER INSTANCE
# =========================================================

_phone_contacts = PhoneContactsController()


# =========================================================
# CALL CONTACT CAPABILITY
# =========================================================

def call_contact(name: str):
    """
    Search for a contact on the Android phone
    and immediately place the call.

    No confirmation is required.
    """

    success = _phone_contacts.call(name)

    if not success:
        return {
            "success": False,
            "action": "call_contact",
            "data": None,
            "error": (
                f"Could not call contact '{name}'."
            ),
        }

    return {
        "success": True,
        "action": "call_contact",
        "data": {
            "contact": name,
            "status": "calling",
        },
        "error": None,
    }


# =========================================================
# REGISTER CAPABILITY
# =========================================================

registry.register(
    name="call_contact",
    description=(
        "Search for a contact on the Android phone "
        "and call them immediately."
    ),
    parameters={
        "type": "object",
        "properties": {
            "name": {
                "type": "string",
                "description": (
                    "Name of the contact to call."
                ),
            }
        },
        "required": ["name"],
    },
    function=call_contact,
    risk="safe",
)