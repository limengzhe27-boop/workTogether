"""Experimental offline macOS Python runner; never falls back to host execution.

This is deliberately NOT wired to Agent tools. Only explicit file snapshots and
a Python entry file are accepted; no shell command, mounts, env or profile input.
Seatbelt is deprecated: probe its actual denials before each execution.
"""
import hashlib
import json
import math
import os
import selectors
import signal
import subprocess
import sys
import tempfile
import time
from pathlib import Path

from .core import CollabError, require
from .read_tools import MAX_FILE_BYTES, ReadTools, validate_tool_files

MAX_OUTPUT = 64 * 1024
MAX_SECONDS = 30
BACKEND = "macos-seatbelt-python-v1"
SANDBOX = Path("/usr/bin/sandbox-exec")

# Applied by the trusted bootstrap before importing any project code. No
# preexec_fn: the application can be multithreaded. The hard limits cannot be
# increased by project code. These are not aggregate disk/RAM quotas.
BOOTSTRAP = r'''
import resource, runpy, sys
resource.setrlimit(resource.RLIMIT_CORE, (0, 0))
resource.setrlimit(resource.RLIMIT_FSIZE, (1048576, 1048576))
resource.setrlimit(resource.RLIMIT_NOFILE, (64, 64))
resource.setrlimit(resource.RLIMIT_CPU, (30, 30))
sys.path.insert(0, ".")
sys.argv = [sys.argv[1]]
runpy.run_path(sys.argv[0], run_name="__main__")
'''

# This fixed program contains only harmless canary checks. It does not read
# real credentials, contact real services or execute project-provided code.
PROBE = r'''
import errno, json, os, socket, subprocess, sys
from pathlib import Path
outside, interpreter = sys.argv[1:]
checks = {}
def denied(name, operation):
    try:
        operation()
    except OSError as exc:
        checks[name] = exc.errno in (errno.EPERM, errno.EACCES)
    else:
        checks[name] = False
Path("inside").write_text("fixture")
checks["workspace_read_write"] = Path("inside").read_text() == "fixture"
denied("outside_read", lambda: Path(outside).read_bytes())
denied("outside_write", lambda: Path(outside).write_text("changed"))
Path("escape").symlink_to(outside)
denied("symlink_read", lambda: Path("escape").read_bytes())
denied("symlink_write", lambda: Path("escape").write_text("changed"))
denied("hardlink", lambda: os.link(outside, "hardlink"))
def connect():
    with socket.socket() as sock:
        sock.settimeout(1)
        sock.connect(("127.0.0.1", 9))
def bind():
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
def unix_bind():
    with socket.socket(socket.AF_UNIX) as sock:
        sock.bind("local.sock")
denied("network_connect", connect)
denied("network_bind", bind)
denied("unix_socket", unix_bind)
denied("subprocess", lambda: subprocess.run([interpreter, "-I", "-S", "-c", "pass"], check=True))
def fork():
    pid = os.fork()
    if pid == 0:
        os._exit(0)
    os.waitpid(pid, 0)
denied("fork", fork)
checks["clean_environment"] = not any(k in os.environ for k in
    ("WT_CANARY_SECRET", "PYTHONPATH", "DYLD_INSERT_LIBRARIES", "SSH_AUTH_SOCK"))
print(json.dumps(checks, sort_keys=True))
'''
PROBE_CHECKS = {"workspace_read_write", "outside_read", "outside_write", "symlink_read",
                "symlink_write", "network_connect", "network_bind", "subprocess", "fork",
                "clean_environment", "hardlink", "unix_socket"}


def _runtime():
    require(sys.platform == "darwin" and SANDBOX.is_file(),
            "No supported local isolation backend; no host fallback", "ISOLATION_UNAVAILABLE")
    interpreter = Path(sys.executable).resolve(strict=True)
    runtime = Path(sys.base_prefix).resolve(strict=True)
    # Framework Python's bin/python is a launcher that spawns Python.app.
    # Execute the real interpreter so process-fork stays denied.
    framework_python = runtime / "Resources/Python.app/Contents/MacOS/Python"
    if framework_python.is_file():
        interpreter = framework_python.resolve(strict=True)
    require(len(runtime.parts) >= 4 and runtime in interpreter.parents,
            "Python needs a dedicated runtime directory for isolation", "ISOLATION_UNAVAILABLE")
    return interpreter, runtime


def _profile():
    # Paths enter through sandbox parameters, never by interpolating Scheme.
    # dyld-support contains Apple's loader rules, not the broader system.sb
    # (which also grants network/IPC). Re-probe the resulting denials every run;
    # unsupported rules block launch, and a probe is not a complete audit.
    return '''(version 1)
(deny default)
(import "dyld-support.sb")
(allow process-exec (literal (param "PYTHON")))
(allow file-read-metadata (path-ancestors (param "WORK")) (path-ancestors (param "RUNTIME")))
(allow file-read* file-map-executable
    (subpath "/System/Library") (subpath "/usr/lib") (subpath (param "RUNTIME")))
(deny file-read* (subpath (param "SITE")))
(allow file-read* file-write* (subpath (param "WORK")))
(allow file-read* (literal "/dev/urandom") (literal "/dev/random"))
(allow file-read* file-write-data (literal "/dev/null"))
(allow sysctl-read)
(allow signal (target self))
'''


def _environment(work):
    # A fresh map, not os.environ.copy(): API keys, proxy credentials, Python
    # hooks, launch-service tokens and inherited user HOME must not propagate.
    return {"HOME": str(work), "TMPDIR": str(work), "PATH": "/usr/bin:/bin",
            "LANG": "en_US.UTF-8", "LC_ALL": "en_US.UTF-8"}


def _stop(process):
    # No child creation is allowed by the profile. Kill the group as well as
    # the leader defensively; reaping the leader is always required.
    try:
        os.killpg(process.pid, signal.SIGKILL)
    except ProcessLookupError:
        pass
    if process.poll() is None:
        process.kill()
    process.wait(timeout=5)


def _capture(argv, cwd, env, timeout):
    """Drain both pipes with a shared byte cap; never communicate() unbounded."""
    started = time.monotonic()
    output = {"stdout": bytearray(), "stderr": bytearray()}
    reason = None
    with subprocess.Popen(argv, cwd=cwd, env=env, stdin=subprocess.DEVNULL,
                          stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                          close_fds=True, start_new_session=True) as process:
        try:
            with selectors.DefaultSelector() as selector:
                for name in output:
                    pipe = getattr(process, name)
                    os.set_blocking(pipe.fileno(), False)
                    selector.register(pipe, selectors.EVENT_READ, name)
                while selector.get_map() or process.poll() is None:
                    remaining = timeout - (time.monotonic() - started)
                    if remaining <= 0:
                        reason = "TIMEOUT"
                        break
                    for key, _ in selector.select(min(remaining, 0.05)):
                        chunk = os.read(key.fd, 8192)
                        if not chunk:
                            selector.unregister(key.fileobj)
                            continue
                        room = MAX_OUTPUT - sum(len(buf) for buf in output.values())
                        output[key.data].extend(chunk[:room])
                        if len(chunk) > room:
                            reason = "OUTPUT_LIMIT"
                            break
                    if reason:
                        break
            if reason:
                _stop(process)
            else:
                process.wait(timeout=max(0.01, timeout - (time.monotonic() - started)))
        except BaseException:
            _stop(process)
            raise
    return {"status": "COMPLETED" if not reason and process.returncode == 0 else "FAILED",
            "reason": reason or ("OK" if process.returncode == 0 else "NONZERO_EXIT"),
            "returncode": process.returncode,
            **{name: bytes(value).decode("utf-8", errors="replace") for name, value in output.items()}}


def _launch(work, code, args, timeout):
    interpreter, runtime = _runtime()
    site = runtime / "lib" / ("python%d.%d" % sys.version_info[:2]) / "site-packages"
    argv = [str(SANDBOX), "-p", _profile(), "-D", "WORK=" + str(work),
            "-D", "PYTHON=" + str(interpreter), "-D", "RUNTIME=" + str(runtime),
            "-D", "SITE=" + str(site), str(interpreter), "-I", "-S", "-B", "-c", code, *args]
    return _capture(argv, work, _environment(work), timeout)


def check_isolation():
    """Explicit offline preflight using an isolated temporary canary, not models."""
    try:
        _runtime()
        with tempfile.TemporaryDirectory(prefix="wt-isolation-probe-") as tmp:
            root = Path(tmp).resolve()
            work = root / "work"
            work.mkdir(mode=0o700)
            canary = root / "outside.txt"
            canary.write_text("unchanged", encoding="utf-8")
            interpreter, _ = _runtime()
            result = _launch(work, PROBE, [str(canary), str(interpreter)], 5)
            try:
                checks = json.loads(result["stdout"])
            except (ValueError, TypeError):
                checks = {}
            passed = (result["status"] == "COMPLETED" and isinstance(checks, dict)
                      and set(checks) == PROBE_CHECKS and all(v is True for v in checks.values())
                      and canary.read_text(encoding="utf-8") == "unchanged")
            return {"backend": BACKEND, "status": "READY" if passed else "BLOCKED",
                    "reason": "PROBE_PASSED" if passed else "ISOLATION_PROBE_FAILED",
                    "checks": checks if isinstance(checks, dict) else {},
                    "probe_returncode": result["returncode"],
                    "diagnostic": result["stderr"][:2000].replace(str(root), "<probe>"),
                    "deprecated_backend": True, "host_fallback": False}
    except (CollabError, OSError, subprocess.SubprocessError):
        return {"backend": BACKEND, "status": "BLOCKED", "reason": "ISOLATION_UNAVAILABLE",
                "checks": {}, "deprecated_backend": True, "host_fallback": False}


def _snapshot(project, state_db, files):
    # Use the existing descriptor-relative no-follow reader, including state
    # exclusions. Recheck its UTF-8 round trip to preserve exact snapshot bytes.
    snapshots = {}
    try:
        with ReadTools(project, state_db, files) as reader:
            for path in files:
                require(not reader._protected(path), "State files cannot enter a sandbox", "TOOL_PATH_DENIED")
                item = reader._read(path, 0, MAX_FILE_BYTES)
                raw = item["content"].encode("utf-8")
                require(not item["truncated"] and hashlib.sha256(raw).hexdigest() == item["excerpt_sha256"],
                        "Isolation inputs must be complete UTF-8 files", "TOOL_PATH_DENIED")
                snapshots[path] = raw
    except OSError as exc:
        raise CollabError("TOOL_PATH_DENIED", "Cannot safely snapshot selected files") from exc
    return snapshots


def run_isolated_python(project, files, entry, *, state_db, timeout=10):
    """Run an explicit Python file in a disposable snapshot, never the project.

    The caller's entry code is untrusted. Output is untrusted text, not proof of
    test success. No artifacts are copied back; no database or Agent is opened.
    """
    files = validate_tool_files(files)
    require(entry in files and entry.endswith(".py"), "Select the Python entry file explicitly", "INVALID_INPUT")
    require(type(timeout) in (int, float) and math.isfinite(timeout) and 0 < timeout <= MAX_SECONDS,
            "Timeout must be finite and between 0 and 30 seconds", "INVALID_INPUT")
    project = Path(project).resolve(strict=True)
    require(project.is_dir(), "Project directory is missing", "INVALID_INPUT")
    # Inputs must not overlap the profile's already-readable SDK/runtime roots,
    # or contain the temporary execution directory. Otherwise unselected source
    # could be visible, or scratch writes could land in the original project.
    for readable in (Path("/System"), Path("/usr/lib"), Path(sys.base_prefix).resolve()):
        require(project != readable and project not in readable.parents and readable not in project.parents,
                "Project overlaps an isolation runtime directory", "ISOLATION_SCOPE_DENIED")
    scratch = Path(tempfile.gettempdir()).resolve()
    require(project != scratch and project not in scratch.parents,
            "Project must not contain the sandbox temporary root", "ISOLATION_SCOPE_DENIED")
    preflight = check_isolation()
    if preflight["status"] != "READY":
        return {"status": "BLOCKED", "reason": preflight["reason"], "isolation": preflight}
    snapshots = _snapshot(project, state_db, files)
    manifest = {path: hashlib.sha256(raw).hexdigest() for path, raw in snapshots.items()}
    with tempfile.TemporaryDirectory(prefix="wt-isolated-python-") as tmp:
        work = Path(tmp).resolve()
        for path, raw in snapshots.items():
            target = work / path
            target.parent.mkdir(parents=True, exist_ok=True)
            # Exclusive creation also rejects case-insensitive collisions.
            with target.open("xb") as output:
                output.write(raw)
            target.chmod(0o600)
        try:
            result = _launch(work, BOOTSTRAP, [entry], timeout)
        except (OSError, subprocess.SubprocessError) as exc:
            raise CollabError("ISOLATION_UNAVAILABLE", "Isolated launch failed; no host fallback") from exc
    return {**result, "backend": BACKEND, "isolation": preflight,
            "input_sha256": manifest, "outputs_copied_back": False}
