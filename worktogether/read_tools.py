"""Opt-in, bounded file reads. No shell, writes, directory discovery or symlinks."""
import hashlib
import json
import os
import stat
from pathlib import Path

from .core import CollabError, require

MAX_CALLS = 4
MAX_READ_BYTES = 12_000
MAX_FILE_BYTES = 1_048_576
MAX_TOTAL_BYTES = MAX_CALLS * MAX_READ_BYTES
DENIED_NAMES = {"secrets.json", "credentials.json", "auth.json", "credentials",
                "id_rsa", "id_ed25519", "node_modules", "venv", "__pycache__"}
DENIED_SUFFIXES = (".pem", ".key", ".p12", ".pfx", ".sqlite", ".sqlite3", ".db")


def validate_tool_files(paths):
    require(isinstance(paths, list) and len(paths) <= 10,
            "tool_files must contain at most 10 explicit relative files", "INVALID_INPUT")
    for path in paths:
        require(isinstance(path, str) and 0 < len(path) <= 512,
                "Invalid tool file path", "INVALID_INPUT")
        parts = path.split("/")
        require(not any(ord(c) < 32 or c in "\\:" for c in path)
                and all(p and not p.startswith(".") and p.lower() not in DENIED_NAMES for p in parts)
                and not path.lower().endswith(DENIED_SUFFIXES),
                "Tool file paths must be relative, non-secret, non-hidden files", "INVALID_INPUT")
    require(len(paths) == len(set(paths)), "Duplicate tool file paths", "INVALID_INPUT")
    return list(paths)


TOOLS = [
    {"type": "function", "function": {
        "name": "list_files", "description": "List only the files the user explicitly authorized for this reply.",
        "parameters": {"type": "object", "properties": {}, "additionalProperties": False}}},
    {"type": "function", "function": {
        "name": "read_file", "description": "Read a bounded UTF-8 excerpt from one authorized project file. No writes or commands.",
        "parameters": {"type": "object", "properties": {
            "path": {"type": "string"}, "offset": {"type": "integer", "minimum": 0, "maximum": MAX_FILE_BYTES},
            "limit": {"type": "integer", "minimum": 1, "maximum": MAX_READ_BYTES}},
            "required": ["path"], "additionalProperties": False}}},
]


class ReadTools:
    def __init__(self, project, state_db, files):
        self.files = validate_tool_files(files)
        self.project = Path(project)
        self.state_db = Path(state_db).resolve()
        self.calls = self.bytes_read = 0
        self.root_fd = None

    def __enter__(self):
        require(os.name == "posix" and hasattr(os, "O_NOFOLLOW") and os.open in os.supports_dir_fd,
                "Secure file reads are unavailable on this platform", "TOOLS_UNAVAILABLE")
        self.root_fd = os.open(self.project, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
        return self

    def __exit__(self, *_):
        if self.root_fd is not None:
            os.close(self.root_fd)
            self.root_fd = None

    def _protected(self, relative):
        target = self.project / relative
        state_dir = self.state_db.parent
        if state_dir != self.project and self.project in state_dir.parents:
            return target == state_dir or state_dir in target.parents
        return (target == self.state_db or str(target).startswith(str(self.state_db) + "-")
                or (target.parent == state_dir and target.name.startswith("execution-")
                    and target.suffix == ".lock")
                or target == state_dir / "runs" or state_dir / "runs" in target.parents)

    def execute(self, name, arguments):
        require(self.calls < MAX_CALLS, "Read tool budget exhausted", "TOOL_LIMIT")
        self.calls += 1  # Denied requests cost budget too.
        try:
            require(isinstance(arguments, str) and len(arguments) <= 4096,
                    "Invalid tool arguments", "TOOL_ARGUMENTS")
            args = json.loads(arguments)
            require(isinstance(args, dict), "Expected an argument object", "TOOL_ARGUMENTS")
            if name == "list_files":
                require(not args, "list_files takes no arguments", "TOOL_ARGUMENTS")
                return {"ok": True, "files": [f for f in self.files if not self._protected(f)],
                        "scope": "explicit files only"}
            require(name == "read_file", "Only list_files and read_file are allowed", "TOOL_NOT_ALLOWED")
            require(set(args) <= {"path", "offset", "limit"} and "path" in args,
                    "Invalid read_file fields", "TOOL_ARGUMENTS")
            path = args["path"]
            require(isinstance(path, str) and path in self.files and not self._protected(path),
                    "File is outside the authorized scope", "TOOL_PATH_DENIED")
            offset, limit = args.get("offset", 0), args.get("limit", MAX_READ_BYTES)
            require(type(offset) is int and 0 <= offset <= MAX_FILE_BYTES
                    and type(limit) is int and 1 <= limit <= MAX_READ_BYTES,
                    "Invalid read range", "TOOL_ARGUMENTS")
            require(self.bytes_read + limit <= MAX_TOTAL_BYTES, "Read byte budget exhausted", "TOOL_LIMIT")
            return self._read(path, offset, limit)
        except CollabError as exc:
            return {"ok": False, "error": exc.code, "message": str(exc)}
        except (ValueError, TypeError):
            return {"ok": False, "error": "TOOL_ARGUMENTS", "message": "Invalid JSON arguments"}
        except OSError:
            # Do not forward OS exceptions that can reveal host paths.
            return {"ok": False, "error": "TOOL_PATH_DENIED", "message": "Cannot safely read the requested file"}

    def _read(self, path, offset, limit):
        # Open each component relative to an already-open directory. Resolving a
        # path then opening it by name is insufficient against symlink swaps.
        require(self.root_fd is not None, "Reader is closed", "TOOLS_UNAVAILABLE")
        directory = os.dup(self.root_fd)
        fd = None
        try:
            parts = path.split("/")
            for part in parts[:-1]:
                child = os.open(part, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=directory)
                os.close(directory)
                directory = child
            fd = os.open(parts[-1], os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=directory)
            before = os.fstat(fd)
            require(stat.S_ISREG(before.st_mode) and before.st_nlink == 1,
                    "Only regular files without hard links are readable", "TOOL_PATH_DENIED")
            require(before.st_size <= MAX_FILE_BYTES, "File exceeds 1 MiB", "TOOL_FILE_TOO_LARGE")
            os.lseek(fd, offset, os.SEEK_SET)
            raw = os.read(fd, limit)
            self.bytes_read += len(raw)
            after = os.fstat(fd)
            require((before.st_size, before.st_mtime_ns) == (after.st_size, after.st_mtime_ns),
                    "File changed while reading; no stable excerpt available", "WORKSPACE_CHANGED")
            require(b"\0" not in raw, "Binary files are not supported", "TOOL_BINARY_FILE")
            return {"ok": True, "path": path, "content": raw.decode("utf-8", errors="replace"),
                    "offset": offset, "next_offset": offset + len(raw), "bytes": len(raw),
                    "size_bytes": after.st_size, "mtime_ns": after.st_mtime_ns,
                    "truncated": offset + len(raw) < after.st_size,
                    "excerpt_sha256": hashlib.sha256(raw).hexdigest()}
        finally:
            if fd is not None:
                os.close(fd)
            os.close(directory)
