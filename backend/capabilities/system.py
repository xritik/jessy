"""
System information capabilities.
"""

from __future__ import annotations

import os
from datetime import datetime, timezone

import psutil

from core.registry import registry
from core.result import CapabilityResult


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
