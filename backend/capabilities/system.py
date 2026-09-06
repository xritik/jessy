"""
System information capabilities.
"""

from __future__ import annotations

import os
import subprocess
import tempfile
import comtypes
import traceback
import wmi
import psutil


from datetime import datetime, timezone
from ctypes import cast, POINTER
from comtypes import CLSCTX_ALL
from pycaw.pycaw import AudioUtilities, IAudioEndpointVolume
from core.registry import registry
from core.result import CapabilityResult

DEBUG_LOG_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "jessy_debug.log")

def _log_error(label: str):
    with open(DEBUG_LOG_PATH, "a", encoding="utf-8") as f:
        f.write(f"\n--- {label} ---\n")
        f.write(traceback.format_exc())
        f.write("\n")


def get_current_time() -> dict:
    """Return the current date and time in UTC and local form."""
    now_utc = datetime.now(timezone.utc)
    now_local = datetime.now()
    return {
        "utc_iso": now_utc.isoformat(),
        "local": now_local.strftime("%Y-%m-%d %H:%M:%S"),
    }


def get_cpu_usage() -> dict:
    """Return current CPU usage percentage and core count."""
    return {
        "cpu_percent": psutil.cpu_percent(interval=0.5),
        "logical_cores": psutil.cpu_count(logical=True),
        "physical_cores": psutil.cpu_count(logical=False),
    }


def get_memory_usage() -> dict:
    """Return current RAM usage."""
    mem = psutil.virtual_memory()
    return {
        "total_gb": round(mem.total / (1024 ** 3), 2),
        "used_gb": round(mem.used / (1024 ** 3), 2),
        "available_gb": round(mem.available / (1024 ** 3), 2),
        "percent_used": mem.percent,
    }


def get_disk_usage(drive: str = "C:\\") -> CapabilityResult:
    """Return disk usage for a given drive or path."""
    resolved = os.path.abspath(os.path.expandvars(drive))
    if not os.path.exists(resolved):
        return CapabilityResult.fail("get_disk_usage", f"Drive or path does not exist: {resolved}")

    usage = psutil.disk_usage(resolved)
    return CapabilityResult.ok("get_disk_usage", {
        "path": resolved,
        "total_gb": round(usage.total / (1024 ** 3), 2),
        "used_gb": round(usage.used / (1024 ** 3), 2),
        "free_gb": round(usage.free / (1024 ** 3), 2),
        "percent_used": usage.percent,
    })


def get_battery_status() -> dict:
    """Return battery status, if the device has a battery."""
    battery = psutil.sensors_battery()
    if battery is None:
        return {"battery_present": False}
    return {
        "battery_present": True,
        "percent": battery.percent,
        "plugged_in": battery.power_plugged,
    }


def get_network_status() -> dict:
    """Return which network interfaces are up."""
    stats = psutil.net_if_stats()
    interfaces = [{"name": name, "is_up": stat.isup} for name, stat in stats.items()]
    return {"interfaces": interfaces}


def disconnect_wifi() -> CapabilityResult:
    """Disconnect the current WiFi connection without turning the radio off."""
    try:
        result = subprocess.run(
            ["netsh", "wlan", "disconnect"],
            capture_output=True, text=True, timeout=10,
        )
    except OSError as exc:
        return CapabilityResult.fail("disconnect_wifi", f"Failed to disconnect WiFi: {exc}")

    if result.returncode != 0:
        return CapabilityResult.fail(
            "disconnect_wifi",
            f"Failed to disconnect WiFi: {result.stderr.strip() or result.stdout.strip()}",
        )
    return CapabilityResult.ok("disconnect_wifi", {"disconnected": True})


def connect_wifi(ssid: str, password: str | None = None) -> CapabilityResult:
    """Connect to a WiFi network by SSID, switching away from any current
    connection. If a saved profile for that network already exists,
    connects directly. If not and a password is given, creates a
    temporary profile first, then connects."""
    ssid = ssid.strip()
    if not ssid:
        return CapabilityResult.fail("connect_wifi", "No SSID provided.")

    try:
        profiles = subprocess.run(
            ["netsh", "wlan", "show", "profiles"],
            capture_output=True, text=True, timeout=10,
        ).stdout
    except OSError as exc:
        return CapabilityResult.fail("connect_wifi", f"Failed to check saved networks: {exc}")

    has_profile = ssid.lower() in profiles.lower()

    if not has_profile:
        if not password:
            return CapabilityResult.fail(
                "connect_wifi",
                f"No saved profile for '{ssid}' and no password was provided. "
                "Give me the password to connect for the first time.",
            )
        profile_xml = f"""<?xml version="1.0"?>
<WLANProfile xmlns="http://www.microsoft.com/networking/WLAN/profile/v1">
    <name>{ssid}</name>
    <SSIDConfig><SSID><name>{ssid}</name></SSID></SSIDConfig>
    <connectionType>ESS</connectionType>
    <connectionMode>manual</connectionMode>
    <MSM><security>
        <authEncryption>
            <authentication>WPA2PSK</authentication>
            <encryption>AES</encryption>
            <useOneX>false</useOneX>
        </authEncryption>
        <sharedKey>
            <keyType>passPhrase</keyType>
            <protected>false</protected>
            <keyMaterial>{password}</keyMaterial>
        </sharedKey>
    </security></MSM>
</WLANProfile>"""
        with tempfile.NamedTemporaryFile(mode="w", suffix=".xml", delete=False) as f:
            f.write(profile_xml)
            profile_path = f.name
        try:
            add_result = subprocess.run(
                ["netsh", "wlan", "add", "profile", f"filename={profile_path}"],
                capture_output=True, text=True, timeout=10,
            )
        finally:
            os.unlink(profile_path)
        if add_result.returncode != 0:
            return CapabilityResult.fail("connect_wifi", f"Failed to save network profile: {add_result.stderr.strip()}")

    try:
        result = subprocess.run(
            ["netsh", "wlan", "connect", f"name={ssid}"],
            capture_output=True, text=True, timeout=15,
        )
    except OSError as exc:
        return CapabilityResult.fail("connect_wifi", f"Failed to connect: {exc}")

    if result.returncode != 0:
        return CapabilityResult.fail(
            "connect_wifi",
            f"Failed to connect to '{ssid}': {result.stderr.strip() or result.stdout.strip()}",
        )

    return CapabilityResult.ok("connect_wifi", {"connected_to": ssid})


def set_wifi_power(action: str) -> CapabilityResult:
    """Turn the WiFi adapter on or off. Requires the server process to be
    running elevated (as Administrator) -- Windows blocks adapter
    enable/disable for non-admin processes."""
    action = action.strip().lower()
    if action not in ("on", "off"):
        return CapabilityResult.fail("set_wifi_power", "action must be 'on' or 'off'.")

    cmdlet = "Enable-NetAdapter" if action == "on" else "Disable-NetAdapter"
    ps_script = (
        f"$a = Get-NetAdapter | Where-Object "
        f"{{$_.InterfaceDescription -match 'Wireless' -or $_.Name -match 'Wi-Fi'}}; "
        f"if (-not $a) {{ exit 2 }}; "
        f"{cmdlet} -Name $a.Name -Confirm:$false"
    )

    try:
        result = subprocess.run(
            ["powershell", "-NoProfile", "-Command", ps_script],
            capture_output=True, text=True, timeout=15,
        )
    except OSError as exc:
        return CapabilityResult.fail("set_wifi_power", f"Failed to turn WiFi {action}: {exc}")

    if result.returncode == 2:
        return CapabilityResult.fail("set_wifi_power", "No WiFi adapter was found.")
    if result.returncode != 0:
        stderr = result.stderr.strip()
        if "access" in stderr.lower() or "denied" in stderr.lower() or "administrator" in stderr.lower():
            return CapabilityResult.fail(
                "set_wifi_power",
                "Turning WiFi on/off requires administrator privileges. "
                "Restart the server as Administrator to enable this.",
            )
        return CapabilityResult.fail("set_wifi_power", f"Failed to turn WiFi {action}: {stderr or result.stdout.strip()}")

    return CapabilityResult.ok("set_wifi_power", {"wifi": action})


def set_bluetooth_power(action: str) -> CapabilityResult:
    """Turn the Bluetooth radio on or off using the WinRT Radio API."""
    action = action.strip().lower()
    if action not in ("on", "off"):
        return CapabilityResult.fail("set_bluetooth_power", "action must be 'on' or 'off'.")

    state = "On" if action == "on" else "Off"
    ps_script = f"""
Add-Type -AssemblyName System.Runtime.WindowsRuntime

Function Await($WinRtTask, $ResultType) {{
    $asTask = ([System.WindowsRuntimeSystemExtensions].GetMethods() | Where-Object {{
        $_.Name -eq 'AsTask' -and $_.GetParameters().Count -eq 1 -and
        $_.GetParameters()[0].ParameterType.Name -eq 'IAsyncOperation`1'
    }})[0]
    $asTaskAsync = $asTask.MakeGenericMethod($ResultType).Invoke($null, @($WinRtTask))
    $asTaskAsync.Wait(-1) | Out-Null
    $asTaskAsync.Result
}}
[Windows.Devices.Radios.Radio,Windows.System.Devices,ContentType=WindowsRuntime] | Out-Null
[Windows.Devices.Radios.RadioAccessStatus,Windows.System.Devices,ContentType=WindowsRuntime] | Out-Null

$access = Await ([Windows.Devices.Radios.Radio]::RequestAccessAsync()) ([Windows.Devices.Radios.RadioAccessStatus])
if ($access -ne 'Allowed') {{ exit 3 }}

$radios = Await ([Windows.Devices.Radios.Radio]::GetRadiosAsync()) ([System.Collections.Generic.IReadOnlyList[Windows.Devices.Radios.Radio]])
$bt = $radios | Where-Object {{ $_.Kind -eq 'Bluetooth' }}
if (-not $bt) {{ exit 2 }}
Await ($bt.SetStateAsync('{state}')) ([Windows.Devices.Radios.RadioAccessStatus]) | Out-Null
"""

    try:
        result = subprocess.run(
            ["powershell", "-NoProfile", "-Command", ps_script],
            capture_output=True, text=True, timeout=15,
        )
    except OSError as exc:
        return CapabilityResult.fail("set_bluetooth_power", f"Failed to turn Bluetooth {action}: {exc}")

    if result.returncode == 3:
        return CapabilityResult.fail(
            "set_bluetooth_power",
            "Windows denied radio access to this process. Run the server as Administrator, "
            "or check Settings > Privacy & Security > Radios permissions.",
        )
    if result.returncode == 2:
        return CapabilityResult.fail("set_bluetooth_power", "No Bluetooth radio was found on this device.")
    if result.returncode != 0:
        return CapabilityResult.fail(
            "set_bluetooth_power",
            f"Failed to turn Bluetooth {action}: {result.stderr.strip() or result.stdout.strip()}",
        )
    # Open Settings so the user can visually confirm the change
    try:
        subprocess.Popen(["explorer.exe", "ms-settings:bluetooth"])
    except OSError:
        pass

    return CapabilityResult.ok("set_bluetooth_power", {"bluetooth": action})


def _get_volume_interface():
    try:
        comtypes.CoInitialize()
    except OSError:
        pass  # already initialized on this thread — safe to ignore

    devices = AudioUtilities.GetSpeakers()
    return devices.EndpointVolume.QueryInterface(IAudioEndpointVolume)


def set_system_volume(level: int) -> CapabilityResult:
    """Set system volume to a specific percentage (0-100)."""
    if not isinstance(level, int) or not (0 <= level <= 100):
        return CapabilityResult.fail("set_system_volume", "Volume level must be an integer between 0 and 100.")

    try:
        volume = _get_volume_interface()
        volume.SetMasterVolumeLevelScalar(level / 100.0, None)
    except Exception:
        _log_error("VOLUME SET FAILED")
        return CapabilityResult.fail("set_system_volume", "Failed to set volume.")

    return CapabilityResult.ok("set_system_volume", {"volume": level})


def get_system_volume() -> CapabilityResult:
    """Get the current system volume percentage."""
    with open(DEBUG_LOG_PATH, "a", encoding="utf-8") as f:
        f.write("get_system_volume() was called\n")

    try:
        volume = _get_volume_interface()
        level = round(volume.GetMasterVolumeLevelScalar() * 100)
    except Exception:
        _log_error("VOLUME READ FAILED")
        return CapabilityResult.fail("get_system_volume", "Failed to read volume.")

    return CapabilityResult.ok("get_system_volume", {"volume": level})


def mute_system_volume(mute: bool) -> CapabilityResult:
    """Mute or unmute system audio."""
    try:
        volume = _get_volume_interface()
        volume.SetMute(1 if mute else 0, None)
    except Exception:
        _log_error("VOLUME MUTE FAILED")
        return CapabilityResult.fail("mute_system_volume", "Failed to change mute state.")

    return CapabilityResult.ok("mute_system_volume", {"muted": mute})


def _get_brightness_interface():
    c = wmi.WMI(namespace="wmi")
    return c.WmiMonitorBrightnessMethods()[0]


def get_system_brightness() -> CapabilityResult:
    """Get the current screen brightness percentage."""
    try:
        c = wmi.WMI(namespace="wmi")
        level = c.WmiMonitorBrightness()[0].CurrentBrightness
    except Exception:
        _log_error("BRIGHTNESS READ FAILED")
        return CapabilityResult.fail("get_system_brightness", "Failed to read brightness.")

    return CapabilityResult.ok("get_system_brightness", {"brightness": level})


def set_system_brightness(level: int) -> CapabilityResult:
    """Set screen brightness to a specific percentage (0-100)."""
    if not isinstance(level, int) or not (0 <= level <= 100):
        return CapabilityResult.fail("set_system_brightness", "Brightness level must be an integer between 0 and 100.")

    try:
        brightness_methods = _get_brightness_interface()
        brightness_methods.WmiSetBrightness(level, 0)
    except Exception:
        _log_error("BRIGHTNESS SET FAILED")
        return CapabilityResult.fail("set_system_brightness", "Failed to set brightness.")

    return CapabilityResult.ok("set_system_brightness", {"brightness": level})


registry.register(
    name="get_current_time",
    function=get_current_time,
    description="Get the current date and time, in both UTC and the server's local time.",
    parameters={"type": "object", "properties": {}, "required": []},
    risk="safe",
)

registry.register(
    name="get_cpu_usage",
    function=get_cpu_usage,
    description="Get the current CPU usage percentage and core counts.",
    parameters={"type": "object", "properties": {}, "required": []},
    risk="safe",
)

registry.register(
    name="get_memory_usage",
    function=get_memory_usage,
    description="Get the current RAM usage in gigabytes and percentage.",
    parameters={"type": "object", "properties": {}, "required": []},
    risk="safe",
)

registry.register(
    name="get_disk_usage",
    function=get_disk_usage,
    description="Get disk space usage (total, used, free) for a drive or path.",
    parameters={
        "type": "object",
        "properties": {
            "drive": {"type": "string", "description": "Drive letter or path, e.g. 'C:\\'. Defaults to C:\\."},
        },
        "required": [],
    },
    risk="safe",
)

registry.register(
    name="get_battery_status",
    function=get_battery_status,
    description="Get the device's battery percentage and charging status, if it has a battery.",
    parameters={"type": "object", "properties": {}, "required": []},
    risk="safe",
)

registry.register(
    name="get_network_status",
    function=get_network_status,
    description="List network interfaces and whether each is currently up/connected.",
    parameters={"type": "object", "properties": {}, "required": []},
    risk="safe",
)

registry.register(
    name="disconnect_wifi",
    function=disconnect_wifi,
    description="Disconnect the current WiFi network connection (radio stays on).",
    parameters={"type": "object", "properties": {}, "required": []},
    risk="moderate",
)

registry.register(
    name="connect_wifi",
    function=connect_wifi,
    description="Connect to a specific WiFi network by SSID, switching away from any current connection. Provide password if connecting for the first time.",
    parameters={
        "type": "object",
        "properties": {
            "ssid": {"type": "string", "description": "The WiFi network name to connect to."},
            "password": {"type": ["string", "null"], "description": "Network password, only needed if there's no saved profile for this SSID yet."},
        },
        "required": ["ssid"],
    },
    risk="moderate",
)

registry.register(
    name="set_wifi_power",
    function=set_wifi_power,
    description="Turn the WiFi radio/adapter fully on or off (not just disconnect). Requires admin privileges.",
    parameters={
        "type": "object",
        "properties": {
            "action": {"type": "string", "enum": ["on", "off"], "description": "Whether to turn WiFi on or off."},
        },
        "required": ["action"],
    },
    risk="moderate",
)

registry.register(
    name="set_bluetooth_power",
    function=set_bluetooth_power,
    description="Turn the Bluetooth radio on or off.",
    parameters={
        "type": "object",
        "properties": {
            "action": {"type": "string", "enum": ["on", "off"], "description": "Whether to turn Bluetooth on or off."},
        },
        "required": ["action"],
    },
    risk="moderate",
)

registry.register(
    name="set_system_volume",
    function=set_system_volume,
    description="Set the system volume to a specific percentage (0-100).",
    parameters={
        "type": "object",
        "properties": {
            "level": {"type": "integer", "minimum": 0, "maximum": 100, "description": "Target volume percentage"},
        },
        "required": ["level"],
    },
    risk="low",
)

registry.register(
    name="get_system_volume",
    function=get_system_volume,
    description="Get the current system volume percentage.",
    parameters={"type": "object", "properties": {}, "required": []},
    risk="low",
)

registry.register(
    name="mute_system_volume",
    function=mute_system_volume,
    description="Mute or unmute system audio.",
    parameters={
        "type": "object",
        "properties": {
            "mute": {"type": "boolean", "description": "True to mute, False to unmute"},
        },
        "required": ["mute"],
    },
    risk="low",
)

registry.register(
    name="get_system_brightness",
    function=get_system_brightness,
    description="Get the current screen brightness percentage.",
    parameters={"type": "object", "properties": {}, "required": []},
    risk="safe",
)

registry.register(
    name="set_system_brightness",
    function=set_system_brightness,
    description="Set the screen brightness to a specific percentage (0-100).",
    parameters={
        "type": "object",
        "properties": {
            "level": {"type": "integer", "minimum": 0, "maximum": 100, "description": "Target brightness percentage"},
        },
        "required": ["level"],
    },
    risk="low",
)
