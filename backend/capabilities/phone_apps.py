import time

import uiautomator2 as u2

from core.registry import registry
from core.result import CapabilityResult


# -------------------------------------------------
# Known Android applications
# -------------------------------------------------

PHONE_APPS = {
    "youtube": "com.google.android.youtube",
    "contacts": "com.google.android.contacts",
}


class PhoneAppController:
    """
    Controls applications on the connected Android phone.

    This controller is ONLY for the phone.

    It must not be confused with desktop/laptop
    application launching.
    """

    def __init__(self):
        self.device = None

    def _get_device(self):
        if self.device is not None:
            return self.device

        try:
            self.device = u2.connect()
            print("[PHONE] Android phone connected.")
            return self.device
        except Exception as exc:
            print(
                "[PHONE] Android phone is not connected: "
                f"{exc}"
            )
            self.device = None
            return None

    def wait(self, seconds=1):
        time.sleep(seconds)

    def current_app(self):
        return self.device.app_current()

    def open_app(self, app_name: str) -> bool:
        """
        Open an Android application by its friendly name.
        """

        device = self._get_device()

        if device is None:
            return False

        name = app_name.strip().casefold()

        package = PHONE_APPS.get(name)

        if not package:
            print(
                f"Phone app not configured: {app_name}"
            )
            return False

        print(
            f"Opening phone app: {app_name}"
        )

        try:
            device.app_start(package)

            self.wait(2)

            current = device.app_current()

            if current.get("package") != package:
                print(
                    f"Phone app did not open correctly. "
                    f"Expected: {package}, "
                    f"Actual: {current.get('package')}"
                )
                return False

            print(
                f"Phone app opened: {app_name}"
            )

            return True

        except Exception as exc:
            print(
                f"Failed to open phone app "
                f"{app_name}: {exc}"
            )
            return False


_phone_apps = PhoneAppController()


# -------------------------------------------------
# JESSY capability
# -------------------------------------------------

def open_phone_app(app_name: str) -> CapabilityResult:
    """
    Open an application on the connected Android phone.

    IMPORTANT:
    This capability is specifically for requests that
    clearly refer to the PHONE, such as:

        "open YouTube app"
        "open YouTube on my phone"
        "open YouTube in my phone"
        "launch YouTube on my phone"

    Generic desktop requests such as:

        "open YouTube"

    should continue using the existing desktop/laptop
    capability.
    """

    app_name = app_name.strip()

    if not app_name:
        return CapabilityResult.fail(
            "open_phone_app",
            "No phone app name was provided.",
        )

    success = _phone_apps.open_app(
        app_name
    )

    if not success:
        return CapabilityResult.fail(
            "open_phone_app",
            (
                f"Could not open '{app_name}' "
                f"on the Android phone."
            ),
        )

    return CapabilityResult.ok(
        "open_phone_app",
        {
            "app": app_name,
            "device": "android_phone",
            "opened": True,
        },
    )


registry.register(
    name="open_phone_app",
    function=open_phone_app,
    description=(
        "Open an application on the connected Android "
        "PHONE. Use this capability ONLY when the user "
        "explicitly refers to the phone, Android phone, "
        "phone app, or says the app should open on/in "
        "their phone. Examples: 'open YouTube app', "
        "'open YouTube on my phone', 'open YouTube in "
        "my phone', 'launch YouTube on my phone'. "
        "Do NOT use this for generic desktop commands "
        "like 'open YouTube'; those should use the "
        "existing desktop application capability."
    ),
    parameters={
        "type": "object",
        "properties": {
            "app_name": {
                "type": "string",
                "description": (
                    "Name of the Android phone application "
                    "to open, such as YouTube or Contacts."
                ),
            }
        },
        "required": ["app_name"],
    },
    risk="safe",
)