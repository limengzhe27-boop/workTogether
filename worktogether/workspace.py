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


IGNORED_DIRS = {".git", ".collab", "node_modules", "__pycache__", ".venv", "venv", "dist", "build", ".next", ".idea"}


def list_project_files(project, limit=200):
    """Return a bounded list of relative file paths inside the project workspace."""
    root = Path(project).resolve()
    if not root.is_dir():
        return []
    try:
        listed = subprocess.run(
            ["git", "ls-files", "--cached", "--others", "--exclude-standard", "-z", "--", "."],
            cwd=root,
            capture_output=True,
            timeout=5,
        )
        if listed.returncode == 0:
            files = []
            for raw in sorted(set(filter(None, listed.stdout.split(b"\0")))):
                rel = os.fsdecode(raw)
                parts = Path(rel).parts
                if any(p in IGNORED_DIRS for p in parts):
                    continue
                if (root / rel).is_file():
                    files.append(rel)
                    if len(files) >= limit:
                        break
            return files
    except (FileNotFoundError, subprocess.TimeoutExpired, OSError):
        pass

    files = []
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = sorted(d for d in dirnames if d not in IGNORED_DIRS and not d.startswith("."))
        for fname in sorted(filenames):
            if fname.startswith("."):
                continue
            full = Path(dirpath) / fname
            try:
                rel = str(full.relative_to(root))
            except ValueError:
                continue
            files.append(rel)
            if len(files) >= limit:
                return files
    return files


def project_files_snapshot(project, limit=250):
    """Return {files:[{path,size}], total, truncated} for the workspace UI."""
    paths = list_project_files(project, limit=limit + 1)
    truncated = len(paths) > limit
    root = Path(project).resolve()
    entries = []
    for rel in paths[:limit]:
        if rel == ".collab" or rel.startswith(".collab/"):
            continue
        try:
            size = (root / rel).stat().st_size
        except OSError:
            continue
        entries.append({"path": rel, "size": size})
    return {"files": entries, "total": len(entries), "truncated": truncated}


README_NAMES = ("README.md", "README.MD", "Readme.md", "readme.md",
                "README.rst", "README.txt", "README")


HASH_HEAD_BYTES = 4096


def file_digests(project, limit=800):
    """{relative path: digest} for the files an agent could have touched.

    The digest folds in size, mtime and the first few KB of content: cheap enough
    to snapshot before and after every turn, precise enough to attribute a change
    to the turn that made it.
    """
    root = Path(project).resolve()
    if not root.is_dir():
        return {}
    digests = {}
    for rel in list_project_files(project, limit=limit):
        if rel == ".collab" or rel.startswith(".collab/"):
            continue
        target = root / rel
        try:
            stat = target.stat()
            with target.open("rb") as stream:
                head = stream.read(HASH_HEAD_BYTES)
        except OSError:
            continue
        digest = hashlib.sha1(f"{stat.st_size}\0{stat.st_mtime_ns}\0".encode("ascii"))
        digest.update(head)
        digests[rel] = digest.hexdigest()
    return digests


def changed_files(before, after):
    """Human-readable delta between two file_digests() snapshots."""
    changes = []
    for path in sorted(set(before) | set(after)):
        if path not in before:
            changes.append({"path": path, "status": "added"})
        elif path not in after:
            changes.append({"path": path, "status": "removed"})
        elif before[path] != after[path]:
            changes.append({"path": path, "status": "modified"})
    return changes


def project_digest(project, max_readme_chars=3000, max_files=120):
    """Bounded project orientation block prepended to every chat dispatch.

    This is what lets a chat-only agent answer "what is this project" without
    guessing, and it must stay small enough to be safe on every message.
    """
    root = Path(project).resolve()
    if not root.is_dir():
        return {}
    digest = {"name": root.name, "path": str(root)}
    for candidate in README_NAMES:
        path = root / candidate
        if path.is_file():
            try:
                text = path.read_text(encoding="utf-8", errors="replace")
            except OSError:
                break
            digest["readme_path"] = candidate
            digest["readme"] = text[:max_readme_chars]
            digest["readme_truncated"] = len(text) > max_readme_chars
            break
    paths = list_project_files(project, limit=max_files + 1)
    digest["files"] = paths[:max_files]
    digest["files_truncated"] = len(paths) > max_files
    return digest



def read_project_files(project, rel_paths, max_bytes_per_file=12000, max_total_bytes=48000):
    """Safely read user-referenced relative files inside the project directory."""
    root = Path(project).resolve()
    if not root.is_dir() or not rel_paths:
        return []
    results = []
    total = 0
    for rel in rel_paths[:10]:
        if not isinstance(rel, str) or not rel.strip():
            continue
        clean_rel = rel.strip().lstrip("/")
        try:
            target = (root / clean_rel).resolve()
        except OSError:
            continue
        if not target.is_relative_to(root) or not target.is_file():
            continue
        try:
            size = target.stat().st_size
            with target.open("rb") as f:
                raw = f.read(max_bytes_per_file + 1)
            truncated = len(raw) > max_bytes_per_file
            if truncated:
                raw = raw[:max_bytes_per_file]
            text = raw.decode("utf-8", errors="replace").replace("DISPATCH:\n", "DISPATCH_:\n")
            total += len(raw)
            results.append({
                "path": clean_rel,
                "content": text,
                "size_bytes": size,
                "truncated": truncated,
            })
            if total >= max_total_bytes:
                break
        except OSError:
            continue
    return results


def project_diff(project, max_diff_bytes=131072):
    """Return Git status file list and unified diff for a project directory."""
    root = Path(project).resolve()
    if not root.is_dir():
        raise CollabError("NOT_FOUND", "Project directory not found")
    try:
        check = subprocess.run(
            ["git", "rev-parse", "--show-toplevel"],
            cwd=root,
            capture_output=True,
            text=True,
            timeout=5,
        )
    except (FileNotFoundError, subprocess.TimeoutExpired):
        return {"is_git": False, "files": [], "diff": ""}
    if check.returncode != 0:
        return {"is_git": False, "files": [], "diff": ""}

    files = []
    try:
        status_proc = subprocess.run(
            ["git", "status", "--porcelain", "-uall", "--", "."],
            cwd=root,
            capture_output=True,
            text=True,
            timeout=10,
        )
        if status_proc.returncode == 0:
            for line in status_proc.stdout.splitlines():
                if len(line) < 4:
                    continue
                code = line[:2].strip() or "M"
                rel = line[3:].strip()
                if rel.startswith(".collab/") or rel == ".collab":
                    continue
                files.append({"status": code, "path": rel})
        diff_proc = subprocess.run(
            ["git", "diff", "HEAD", "--", "."],
            cwd=root,
            capture_output=True,
            timeout=10,
        )
        raw_diff = diff_proc.stdout if diff_proc.returncode == 0 else b""
        truncated = len(raw_diff) > max_diff_bytes
        diff_text = raw_diff[:max_diff_bytes].decode("utf-8", errors="replace")
        return {
            "is_git": True,
            "files": files,
            "diff": diff_text,
            "truncated": truncated,
        }
    except (subprocess.TimeoutExpired, OSError):
        return {"is_git": True, "files": files, "diff": "", "truncated": False}


def revert_project_file(project, rel_path):
    """Revert a single modified or untracked file in the project workspace."""
    root = Path(project).resolve()
    if not isinstance(rel_path, str) or not rel_path.strip():
        raise CollabError("INVALID_INPUT", "File path required")
    clean_rel = rel_path.strip().lstrip("/")
    target = (root / clean_rel).resolve()
    if not target.is_relative_to(root) or clean_rel.startswith(".collab"):
        raise CollabError("FORBIDDEN", "Path outside project workspace")
    try:
        tracked = subprocess.run(
            ["git", "ls-files", "--error-unmatch", "--", clean_rel],
            cwd=root,
            capture_output=True,
            timeout=5,
        )
        if tracked.returncode == 0:
            res = subprocess.run(
                ["git", "checkout", "HEAD", "--", clean_rel],
                cwd=root,
                capture_output=True,
                text=True,
                timeout=10,
            )
            if res.returncode != 0:
                raise CollabError("WORKSPACE_UNAVAILABLE", res.stderr.strip() or "Git checkout failed")
            return {"reverted": clean_rel, "mode": "checkout"}
        elif target.is_file():
            target.unlink()
            return {"reverted": clean_rel, "mode": "removed_untracked"}
        else:
            raise CollabError("NOT_FOUND", "File not found in workspace")
    except subprocess.TimeoutExpired as exc:
        raise CollabError("WORKSPACE_UNAVAILABLE", "Git revert timed out") from exc

