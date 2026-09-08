"""
Filesystem capabilities.

Paths accept "~", environment variables, and relative paths, and are
resolved to absolute paths before use. Destructive operations (delete,
move, overwrite) are tagged risk="confirm" for the future Safety Layer.
"""

from __future__ import annotations

import os
import re
import shutil
import subprocess

from capabilities.guard import is_protected_path
from core.registry import registry
from core.result import CapabilityResult


def _resolve(path: str) -> str:
    return os.path.abspath(os.path.expanduser(os.path.expandvars(path)))


def list_directory(path: str = ".") -> CapabilityResult:
    """List files and folders inside the given directory."""
    resolved = _resolve(path)

    if not os.path.exists(resolved):
        return CapabilityResult.fail("list_directory", f"Path does not exist: {resolved}")
    if not os.path.isdir(resolved):
        return CapabilityResult.fail("list_directory", f"Path is not a directory: {resolved}")

    entries = []
    with os.scandir(resolved) as it:
        for entry in it:
            try:
                stat = entry.stat()
                entries.append({
                    "name": entry.name,
                    "type": "directory" if entry.is_dir() else "file",
                    "size_bytes": stat.st_size if entry.is_file() else None,
                })
            except OSError:
                continue

    return CapabilityResult.ok("list_directory", {"path": resolved, "entries": entries})


def read_file(path: str, max_chars: int = 5000) -> CapabilityResult:
    """Read the text contents of a file, truncated to max_chars."""
    resolved = _resolve(path)

    if not os.path.exists(resolved):
        return CapabilityResult.fail("read_file", f"File does not exist: {resolved}")
    if not os.path.isfile(resolved):
        return CapabilityResult.fail("read_file", f"Path is not a file: {resolved}")

    try:
        with open(resolved, "r", encoding="utf-8", errors="replace") as f:
            content = f.read(max_chars + 1)
    except PermissionError:
        return CapabilityResult.fail("read_file", f"Permission denied: {resolved}")

    truncated = len(content) > max_chars
    return CapabilityResult.ok("read_file", {
        "path": resolved,
        "content": content[:max_chars],
        "truncated": truncated,
    })


def write_file(path: str, content: str, overwrite: bool = False) -> CapabilityResult:
    """Create a new file with the given content. Fails if it exists unless overwrite=True."""
    resolved = _resolve(path)

    if is_protected_path(os.path.dirname(resolved) or resolved):
        return CapabilityResult.fail("write_file", f"Refusing to write into protected system path: {resolved}")

    if os.path.exists(resolved) and not overwrite:
        return CapabilityResult.fail("write_file", f"File already exists (set overwrite=true to replace it): {resolved}")

    try:
        os.makedirs(os.path.dirname(resolved), exist_ok=True)
        with open(resolved, "w", encoding="utf-8") as f:
            f.write(content)
    except PermissionError:
        return CapabilityResult.fail("write_file", f"Permission denied: {resolved}")

    return CapabilityResult.ok("write_file", {"path": resolved, "bytes_written": len(content.encode("utf-8"))})


def create_directory(path: str) -> CapabilityResult:
    """Create a directory, including any missing parent directories."""
    resolved = _resolve(path)

    if is_protected_path(resolved):
        return CapabilityResult.fail("create_directory", f"Refusing to create inside protected system path: {resolved}")

    try:
        os.makedirs(resolved, exist_ok=True)
    except PermissionError:
        return CapabilityResult.fail("create_directory", f"Permission denied: {resolved}")

    return CapabilityResult.ok("create_directory", {"path": resolved})


def delete_path(path: str, recursive: bool = False) -> CapabilityResult:
    """Delete a file, or a directory tree if recursive=True."""
    resolved = _resolve(path)

    if is_protected_path(resolved):
        return CapabilityResult.fail("delete_path", f"Refusing to delete protected system path: {resolved}")
    if not os.path.exists(resolved):
        return CapabilityResult.fail("delete_path", f"Path does not exist: {resolved}")

    try:
        if os.path.isdir(resolved):
            if not recursive:
                return CapabilityResult.fail(
                    "delete_path",
                    f"'{resolved}' is a directory. Set recursive=true to delete it and its contents.",
                )
            shutil.rmtree(resolved)
        else:
            os.remove(resolved)
    except PermissionError:
        return CapabilityResult.fail("delete_path", f"Permission denied: {resolved}")

    return CapabilityResult.ok("delete_path", {"path": resolved, "deleted": True})


def move_path(source: str, destination: str) -> CapabilityResult:
    """Move or rename a file or directory."""
    src = _resolve(source)
    dst = _resolve(destination)

    if is_protected_path(src) or is_protected_path(dst):
        return CapabilityResult.fail("move_path", "Refusing to move a protected system path.")
    if not os.path.exists(src):
        return CapabilityResult.fail("move_path", f"Source does not exist: {src}")

    try:
        shutil.move(src, dst)
    except PermissionError:
        return CapabilityResult.fail("move_path", "Permission denied during move.")
    except shutil.Error as exc:
        return CapabilityResult.fail("move_path", str(exc))

    return CapabilityResult.ok("move_path", {"source": src, "destination": dst})


def copy_path(source: str, destination: str) -> CapabilityResult:
    """Copy a file or directory to a new location, leaving the original intact."""
    src = _resolve(source)
    dst = _resolve(destination)

    if not os.path.exists(src):
        return CapabilityResult.fail("copy_path", f"Source does not exist: {src}")

    try:
        if os.path.isdir(src):
            shutil.copytree(src, dst)
        else:
            os.makedirs(os.path.dirname(dst), exist_ok=True)
            shutil.copy2(src, dst)
    except PermissionError:
        return CapabilityResult.fail("copy_path", "Permission denied during copy.")
    except shutil.Error as exc:
        return CapabilityResult.fail("copy_path", str(exc))

    return CapabilityResult.ok("copy_path", {"source": src, "destination": dst})


_SEARCH_ROOTS = ["~", "~/Desktop", "~/Documents", "~/Downloads", "~/Pictures", "~/Videos", "~/Music", "~/OneDrive"]
_SKIP_DIR_NAMES = {"node_modules", "__pycache__", ".git", "AppData", "$Recycle.Bin", ".venv", "venv"}


def _normalize(s: str) -> str:
    """Collapse -, _, and whitespace into single spaces for loose comparison,
    so 'home_services', 'home-services', and 'home services' all match."""
    return re.sub(r"[\s\-_]+", " ", s.strip().lower())


def _is_match(query_norm: str, query_tokens: set[str], candidate_name: str) -> bool:
    """True if candidate_name matches the query either as a substring
    (ignoring separator style) or by containing all query words/tokens
    regardless of order."""
    name_norm = _normalize(candidate_name)
    if query_norm and query_norm in name_norm:
        return True
    name_tokens = set(name_norm.split())
    return bool(query_tokens) and query_tokens.issubset(name_tokens)


def find_path(name: str, type: str = "any", max_results: int = 5) -> CapabilityResult:
    """Search common user locations (home, Desktop, Documents, Downloads,
    Pictures, Videos, Music, OneDrive) for a file or folder whose name
    matches the given text. Matching ignores differences in separators
    (-, _, space) and word order, so the caller doesn't need to know the
    exact spelling or path. Use this whenever the user refers to a file or
    folder by name without giving its full location."""
    query_norm = _normalize(name)
    query_tokens = set(query_norm.split())
    if not query_norm:
        return CapabilityResult.fail("find_path", "No search term given.")

    matches: list[dict] = []
    visited_roots: set[str] = set()

    for root in _SEARCH_ROOTS:
        resolved_root = _resolve(root)
        if resolved_root in visited_roots or not os.path.isdir(resolved_root):
            continue
        visited_roots.add(resolved_root)

        for dirpath, dirnames, filenames in os.walk(resolved_root):
            depth = dirpath[len(resolved_root):].count(os.sep)
            if depth >= 4:
                dirnames[:] = []
                continue
            dirnames[:] = [d for d in dirnames if d not in _SKIP_DIR_NAMES and not d.startswith(".")]

            if type in ("any", "directory"):
                for d in dirnames:
                    if _is_match(query_norm, query_tokens, d):
                        matches.append({"path": os.path.join(dirpath, d), "type": "directory"})
            if type in ("any", "file"):
                for f in filenames:
                    if _is_match(query_norm, query_tokens, f):
                        matches.append({"path": os.path.join(dirpath, f), "type": "file"})

            if len(matches) >= max_results:
                break
        if len(matches) >= max_results:
            break

    if not matches:
        return CapabilityResult.fail(
            "find_path",
            f"No file or folder matching '{name}' found in common locations "
            "(home, Desktop, Documents, Downloads, Pictures, Videos, Music, OneDrive).",
        )

    return CapabilityResult.ok("find_path", {"query": name, "matches": matches[:max_results]})


def open_folder(path: str = "~") -> CapabilityResult:
    """Open a folder directly in File Explorer using explorer.exe on its
    real filesystem path. Never route this through os.startfile with a
    file:// URI or through a browser opener — Windows resolves the
    file:// scheme via the OS's default *browser* handler (Edge/Chrome),
    not File Explorer, which is exactly what this avoids."""
    resolved = _resolve(path)

    if not os.path.exists(resolved):
        return CapabilityResult.fail("open_folder", f"Path does not exist: {resolved}")
    if not os.path.isdir(resolved):
        return CapabilityResult.fail("open_folder", f"Path is not a directory: {resolved}")

    try:
        subprocess.Popen(["explorer.exe", resolved])
    except OSError as exc:
        return CapabilityResult.fail("open_folder", f"Failed to open folder: {exc}")

    return CapabilityResult.ok("open_folder", {"path": resolved})


def open_file_in_vscode(path: str) -> CapabilityResult:
    """Open a specific file as a tab in the currently running VS Code
    window (reuses the existing window via `code -r`, does not spawn a
    new one). Call this after write_file/create_directory whenever the
    user is working in VS Code and expects to actually see the file —
    writing to disk alone does not open it as a tab."""
    resolved = _resolve(path)

    if not os.path.exists(resolved):
        return CapabilityResult.fail("open_file_in_vscode", f"File does not exist: {resolved}")

    code_cli = shutil.which("code") or shutil.which("code.cmd")
    if not code_cli:
        return CapabilityResult.fail("open_file_in_vscode", "VS Code CLI ('code') not found on PATH.")

    try:
        subprocess.Popen([code_cli, "-r", resolved])
    except OSError as exc:
        return CapabilityResult.fail("open_file_in_vscode", f"Failed to open file in VS Code: {exc}")

    return CapabilityResult.ok("open_file_in_vscode", {"path": resolved})



registry.register(
    name="list_directory",
    function=list_directory,
    description="List the files and subfolders inside a given directory path.",
    parameters={
        "type": "object",
        "properties": {
            "path": {"type": "string", "description": "Directory path to list. Defaults to the current directory."},
        },
        "required": [],
    },
    risk="safe",
)

registry.register(
    name="find_path",
    function=find_path,
    description=(
        "Search common user locations (home, Desktop, Documents, "
        "Downloads, Pictures, Videos, Music, OneDrive) for a FILE OR "
        "FOLDER by name, when the exact path is unknown. Matching ignores "
        "differences in separators (-, _, space) and word order, so pass "
        "the name as the user said it — do NOT try multiple separator "
        "variants yourself, one call is enough. Use this instead of asking "
        "the user for a path whenever they reference something by name "
        "only, e.g. 'open the jessy folder' or 'find my resume'. Returns "
        "one or more matching paths with their type ('file' or 'directory')."
    ),
    parameters={
        "type": "object",
        "properties": {
            "name": {"type": "string", "description": "Name or partial name to search for."},
            "type": {
                "type": "string",
                "enum": ["any", "file", "directory"],
                "description": "Restrict results to files only, folders only, or any. Defaults to 'any'.",
            },
            "max_results": {"type": "integer", "description": "Maximum number of matches to return. Defaults to 5."},
        },
        "required": ["name"],
    },
    risk="safe",
)

registry.register(
    name="read_file",
    function=read_file,
    description="Read and return the text contents of a file.",
    parameters={
        "type": "object",
        "properties": {
            "path": {"type": "string", "description": "Path to the file to read."},
            "max_chars": {"type": "integer", "description": "Maximum characters to return. Defaults to 5000."},
        },
        "required": ["path"],
    },
    risk="safe",
)

registry.register(
    name="write_file",
    function=write_file,
    description="Create a new text file with the given content, or overwrite an existing one if overwrite=true.",
    parameters={
        "type": "object",
        "properties": {
            "path": {"type": "string", "description": "Path of the file to write."},
            "content": {"type": "string", "description": "Text content to write into the file."},
            "overwrite": {"type": "boolean", "description": "Whether to overwrite if the file already exists. Defaults to false."},
        },
        "required": ["path", "content"],
    },
    risk="confirm",
)

registry.register(
    name="create_directory",
    function=create_directory,
    description="Create a new folder, including any missing parent folders.",
    parameters={
        "type": "object",
        "properties": {
            "path": {"type": "string", "description": "Path of the directory to create."},
        },
        "required": ["path"],
    },
    risk="safe",
)

registry.register(
    name="delete_path",
    function=delete_path,
    description="Delete a file, or a directory and all its contents if recursive=true. This cannot be undone.",
    parameters={
        "type": "object",
        "properties": {
            "path": {"type": "string", "description": "Path of the file or directory to delete."},
            "recursive": {"type": "boolean", "description": "Required to be true to delete a non-empty directory."},
        },
        "required": ["path"],
    },
    risk="confirm",
)

registry.register(
    name="move_path",
    function=move_path,
    description="Move or rename a file or directory to a new location.",
    parameters={
        "type": "object",
        "properties": {
            "source": {"type": "string", "description": "Current path of the file or directory."},
            "destination": {"type": "string", "description": "New path or new name."},
        },
        "required": ["source", "destination"],
    },
    risk="confirm",
)

registry.register(
    name="copy_path",
    function=copy_path,
    description="Copy a file or directory to a new location, leaving the original in place.",
    parameters={
        "type": "object",
        "properties": {
            "source": {"type": "string", "description": "Path of the file or directory to copy."},
            "destination": {"type": "string", "description": "Path to copy it to."},
        },
        "required": ["source", "destination"],
    },
    risk="safe",
)

registry.register(
    name="open_folder",
    function=open_folder,
    description=(
        "Open a folder directly in File Explorer by its real filesystem "
        "path (e.g. 'C:\\Users\\me\\Desktop\\jessy', or shortcuts like "
        "'~', 'Desktop'). Use this for ANY request to open File Explorer "
        "or a specific folder — never use open_url for folders, since "
        "file:// URIs get hijacked by the default browser instead of "
        "opening Explorer. Defaults to the home folder if no path is given."
    ),
    parameters={
        "type": "object",
        "properties": {
            "path": {"type": "string", "description": "Folder path to open. Defaults to the home directory ('~')."},
        },
        "required": [],
    },
    risk="safe",
)

registry.register(
    name="open_file_in_vscode",
    function=open_file_in_vscode,
    description=(
        "Open a specific file as a visible tab in the currently running "
        "VS Code window. ALWAYS call this immediately after write_file "
        "when the task involves VS Code (e.g. 'open X in VS Code and "
        "create a file...') — write_file only writes to disk, it never "
        "opens the file as a tab on its own."
    ),
    parameters={
        "type": "object",
        "properties": {
            "path": {"type": "string", "description": "Path to the file to open in VS Code."},
        },
        "required": ["path"],
    },
    risk="safe",
)