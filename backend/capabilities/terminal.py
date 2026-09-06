"""
Terminal / shell command execution capabilities.
"""

from __future__ import annotations

import os
import subprocess
import uuid
from typing import Optional

from core.registry import registry
from core.result import CapabilityResult

MAX_OUTPUT_CHARS = 4000

# Tracks background processes started via start_background_process
_background_processes: dict[str, subprocess.Popen] = {}


def _truncate(text: str) -> str:
    if len(text) > MAX_OUTPUT_CHARS:
        return text[:MAX_OUTPUT_CHARS] + f"\n...[truncated, {len(text) - MAX_OUTPUT_CHARS} more characters]"
    return text


def _build_exec_args(command: str, shell: str) -> list[str]:
    if shell == "powershell":
        return ["powershell", "-NoProfile", "-NonInteractive", "-Command", command]
    return ["cmd", "/c", command]


def _resolve_dir(working_dir: Optional[str]) -> tuple[Optional[str], Optional[str]]:
    """Returns (resolved_path, error_message)."""
    if not working_dir:
        return None, None
    resolved = os.path.abspath(os.path.expandvars(working_dir))
    if not os.path.isdir(resolved):
        return None, f"Working directory does not exist: {resolved}"
    return resolved, None


def run_command(
    command: str,
    shell: str = "powershell",
    timeout: int = 30,
    working_dir: Optional[str] = None,
) -> CapabilityResult:
    """Run a shell command and return its output. Use for short-lived commands
    that finish within the timeout (e.g. 'dir', 'git status', 'pip install x')."""
    command = command.strip()
    if not command:
        return CapabilityResult.fail("run_command", "No command provided.")

    shell = shell.strip().lower()
    if shell not in ("powershell", "cmd"):
        return CapabilityResult.fail("run_command", "shell must be 'powershell' or 'cmd'.")

    resolved_dir, err = _resolve_dir(working_dir)
    if err:
        return CapabilityResult.fail("run_command", err)

    try:
        result = subprocess.run(
            _build_exec_args(command, shell),
            capture_output=True,
            text=True,
            timeout=timeout,
            cwd=resolved_dir,
        )
    except subprocess.TimeoutExpired:
        return CapabilityResult.fail("run_command", f"Command timed out after {timeout} seconds.")
    except OSError as exc:
        return CapabilityResult.fail("run_command", f"Failed to run command: {exc}")

    return CapabilityResult.ok("run_command", {
        "exit_code": result.returncode,
        "stdout": _truncate(result.stdout.strip()),
        "stderr": _truncate(result.stderr.strip()),
        "success": result.returncode == 0,
    })


def start_background_process(
    command: str,
    shell: str = "powershell",
    working_dir: Optional[str] = None,
) -> CapabilityResult:
    """Start a long-running command (e.g. a dev server) in the background
    without blocking. Returns a process_id used to check status or stop it later."""
    command = command.strip()
    if not command:
        return CapabilityResult.fail("start_background_process", "No command provided.")

    shell = shell.strip().lower()
    if shell not in ("powershell", "cmd"):
        return CapabilityResult.fail("start_background_process", "shell must be 'powershell' or 'cmd'.")

    resolved_dir, err = _resolve_dir(working_dir)
    if err:
        return CapabilityResult.fail("start_background_process", err)

    try:
        proc = subprocess.Popen(
            _build_exec_args(command, shell),
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            cwd=resolved_dir,
        )
    except OSError as exc:
        return CapabilityResult.fail("start_background_process", f"Failed to start process: {exc}")

    process_id = str(uuid.uuid4())[:8]
    _background_processes[process_id] = proc

    return CapabilityResult.ok("start_background_process", {
        "process_id": process_id,
        "pid": proc.pid,
        "command": command,
    })


def get_background_process_status(process_id: str) -> CapabilityResult:
    """Check whether a background process started earlier is still running."""
    proc = _background_processes.get(process_id)
    if proc is None:
        return CapabilityResult.fail(
            "get_background_process_status",
            f"No background process found with id '{process_id}'.",
        )

    running = proc.poll() is None
    return CapabilityResult.ok("get_background_process_status", {
        "process_id": process_id,
        "pid": proc.pid,
        "running": running,
        "exit_code": None if running else proc.returncode,
    })


def stop_background_process(process_id: str) -> CapabilityResult:
    """Terminate a background process started earlier via start_background_process."""
    proc = _background_processes.get(process_id)
    if proc is None:
        return CapabilityResult.fail(
            "stop_background_process",
            f"No background process found with id '{process_id}'.",
        )

    if proc.poll() is not None:
        del _background_processes[process_id]
        return CapabilityResult.ok("stop_background_process", {
            "process_id": process_id, "already_stopped": True,
        })

    try:
        proc.terminate()
        proc.wait(timeout=5)
    except subprocess.TimeoutExpired:
        proc.kill()
    except OSError as exc:
        return CapabilityResult.fail("stop_background_process", f"Failed to stop process: {exc}")

    del _background_processes[process_id]
    return CapabilityResult.ok("stop_background_process", {"process_id": process_id, "stopped": True})


registry.register(
    name="run_command",
    function=run_command,
    description="Run a shell command (PowerShell or CMD) and return its output. Use for quick commands that finish within the timeout.",
    parameters={
        "type": "object",
        "properties": {
            "command": {"type": "string", "description": "The command to execute."},
            "shell": {"type": "string", "enum": ["powershell", "cmd"], "description": "Which shell to run the command in. Defaults to powershell."},
            "timeout": {"type": "integer", "description": "Max seconds to wait before giving up. Defaults to 30."},
            "working_dir": {"type": ["string", "null"], "description": "Directory to run the command in. Defaults to the server's working directory."},
        },
        "required": ["command"],
    },
    risk="high",
)

registry.register(
    name="start_background_process",
    function=start_background_process,
    description="Start a long-running command (e.g. a dev server) in the background without blocking. Returns a process_id to check status or stop it later.",
    parameters={
        "type": "object",
        "properties": {
            "command": {"type": "string", "description": "The command to execute."},
            "shell": {"type": "string", "enum": ["powershell", "cmd"], "description": "Which shell to run the command in. Defaults to powershell."},
            "working_dir": {"type": ["string", "null"], "description": "Directory to run the command in."},
        },
        "required": ["command"],
    },
    risk="high",
)

registry.register(
    name="get_background_process_status",
    function=get_background_process_status,
    description="Check whether a background process started earlier is still running, and see its exit code if it finished.",
    parameters={
        "type": "object",
        "properties": {
            "process_id": {"type": "string", "description": "The process_id returned by start_background_process."},
        },
        "required": ["process_id"],
    },
    risk="safe",
)

registry.register(
    name="stop_background_process",
    function=stop_background_process,
    description="Terminate a background process started earlier via start_background_process.",
    parameters={
        "type": "object",
        "properties": {
            "process_id": {"type": "string", "description": "The process_id returned by start_background_process."},
        },
        "required": ["process_id"],
    },
    risk="moderate",
)
