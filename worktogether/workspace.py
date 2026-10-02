"""Best-effort fingerprint of Git-visible project files for execution handoffs.

This detects drift; it does not create an immutable snapshot or cover ignored
files, symlink targets, or edits racing the final check.
"""
import hashlib
import os
import subprocess
from pathlib import Path

from .core import CollabError


def fingerprint(project, state_db):
    """Return a content digest, or None when the project is not a Git checkout."""
    project = Path(project).resolve()
    try:
        check = subprocess.run(["git", "rev-parse", "--show-toplevel"], cwd=project,
                               capture_output=True, timeout=10)
    except FileNotFoundError:
        return None
    except subprocess.TimeoutExpired as exc:
        raise CollabError("WORKSPACE_UNAVAILABLE", "Git check timed out") from exc
    if check.returncode != 0:
        return None
    try:
        listed = subprocess.run(["git", "ls-files", "--cached", "--others",
                                 "--exclude-standard", "-z", "--", "."],
                                cwd=project, capture_output=True, timeout=30)
    except subprocess.TimeoutExpired as exc:
        raise CollabError("WORKSPACE_UNAVAILABLE", "Git file listing timed out") from exc
    if listed.returncode != 0:
        raise CollabError("WORKSPACE_UNAVAILABLE", "Cannot enumerate project files for verification")
    state_db = Path(state_db).resolve()
    state_directory = state_db.parent
    try:
        state_relative = state_directory.relative_to(project)
    except ValueError:
        state_relative = None
    digest = hashlib.sha256()
    for raw in sorted(set(filter(None, listed.stdout.split(b"\0")))):
        relative = os.fsdecode(raw)
        path = project / relative
        # The platform's own database and run artifacts are not project code.
        if (state_relative is not None and state_relative != Path(".")
                and (path == state_directory or state_directory in path.parents)):
            continue
        if (state_relative == Path(".")
                and (path == state_db or path == project / "runs"
                     or project / "runs" in path.parents
                     or (path.parent == project and path.name.startswith("execution-")
                         and path.suffix == ".lock"))):
            continue
        digest.update(raw + b"\0")
        try:
            if path.is_symlink():
                digest.update(b"link\0" + os.fsencode(os.readlink(path)))
            elif path.is_file():
                digest.update(b"file\0")
                digest.update(b"x" if os.access(path, os.X_OK) else b"-")
                with path.open("rb") as stream:
                    for chunk in iter(lambda: stream.read(1024 * 1024), b""):
                        digest.update(chunk)
            elif path.exists():
                # Git submodule content cannot be pinned by this simple guard.
                raise CollabError("WORKSPACE_UNAVAILABLE", "Submodule requires a pinned checkout")
            else:
                digest.update(b"missing\0")
        except OSError as exc:
            raise CollabError("WORKSPACE_UNAVAILABLE", "Cannot read project file for verification") from exc
        digest.update(b"\0")
    return digest.hexdigest()
