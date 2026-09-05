"""
Process management capabilities, backed by psutil.
"""

from __future__ import annotations

from typing import Optional

import psutil

from capabilities.guard import is_protected_process
from core.registry import registry
from core.result import CapabilityResult


def list_processes(limit: int = 15, sort_by: str = "memory") -> CapabilityResult:
    """List running processes, sorted by memory or CPU usage."""
    procs = []
    for p in psutil.process_iter(["pid", "name", "memory_info"]):
        try:
            info = p.info
            procs.append({
                "pid": info["pid"],
                "name": info["name"],
                "cpu_percent": p.cpu_percent(interval=None),
                "memory_mb": round(info["memory_info"].rss / (1024 * 1024), 1) if info["memory_info"] else 0.0,
            })
        except (psutil.NoSuchProcess, psutil.AccessDenied):
            continue

    key = "cpu_percent" if sort_by == "cpu" else "memory_mb"
    procs.sort(key=lambda x: x[key], reverse=True)

    return CapabilityResult.ok("list_processes", {"processes": procs[:limit]})


def kill_process(pid: Optional[int] = None, name: Optional[str] = None) -> CapabilityResult:
    """Terminate a process by PID or by exact process name."""
    if pid is None and not name:
        return CapabilityResult.fail("kill_process", "Provide either a pid or a process name.")

    targets = []
    if pid is not None:
        try:
            targets.append(psutil.Process(pid))
        except psutil.NoSuchProcess:
            return CapabilityResult.fail("kill_process", f"No process with PID {pid}.")
    else:
        targets = [p for p in psutil.process_iter(["name"]) if p.info["name"].lower() == name.lower()]
        if not targets:
            return CapabilityResult.fail("kill_process", f"No running process named '{name}'.")

    killed = []
    for proc in targets:
        if is_protected_process(proc.name()):
            return CapabilityResult.fail("kill_process", f"Refusing to kill critical system process: {proc.name()}")
        try:
            proc.terminate()
            killed.append({"pid": proc.pid, "name": proc.name()})
        except psutil.NoSuchProcess:
            continue
        except psutil.AccessDenied:
            return CapabilityResult.fail(
                "kill_process", f"Permission denied terminating {proc.name()} (pid {proc.pid})."
            )

    return CapabilityResult.ok("kill_process", {"killed": killed})


registry.register(
    name="list_processes",
    function=list_processes,
    description="List currently running processes, sorted by memory or CPU usage.",
    parameters={
        "type": "object",
        "properties": {
            "limit": {"type": "integer", "description": "Maximum number of processes to return. Defaults to 15."},
            "sort_by": {"type": "string", "enum": ["memory", "cpu"], "description": "Sort key. Defaults to 'memory'."},
        },
        "required": [],
    },
    risk="safe",
)

registry.register(
    name="kill_process",
    function=kill_process,
    description="Terminate a running process by PID or by process name. This cannot be undone and may cause unsaved data loss.",
    parameters={
        "type": "object",
        "properties": {
            "pid": {"type": "integer", "description": "Process ID to terminate."},
            "name": {"type": "string", "description": "Exact process name to terminate (e.g. 'notepad.exe')."},
        },
        "required": [],
    },
    risk="confirm",
)
