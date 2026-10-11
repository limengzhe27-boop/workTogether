"""Offline fixtures only. Native tests require real OS isolation, not Agents."""
import contextlib
import fcntl
import hashlib
import io
import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from worktogether import isolation
from worktogether.__main__ import main, parser, run
from worktogether.core import CollabError


class IsolationContractTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name).resolve()
        self.project = self.root / "project"
        self.project.mkdir()
        self.source = self.project / "main.py"
        self.source.write_text("print('fixture')\n")
        self.db = self.project / "state" / "state.sqlite3"

    def execute(self, **kwargs):
        return isolation.run_isolated_python(self.project, kwargs.pop("files", ["main.py"]),
                                             kwargs.pop("entry", "main.py"), state_db=self.db, **kwargs)

    def test_unsupported_platform_is_blocked_and_never_launches(self):
        with patch.object(isolation.sys, "platform", "linux"), patch.object(isolation, "_capture") as launch:
            result = self.execute()
        self.assertEqual("BLOCKED", result["status"])
        self.assertFalse(result["isolation"]["host_fallback"])
        launch.assert_not_called()

    def test_failed_preflight_never_reads_project_or_starts_payload(self):
        with patch.object(isolation, "check_isolation", return_value={"status": "BLOCKED", "reason": "fixture"}), \
                patch.object(isolation, "_snapshot") as snapshot, patch.object(isolation, "_launch") as launch:
            self.assertEqual("BLOCKED", self.execute()["status"])
        snapshot.assert_not_called()
        launch.assert_not_called()

    def test_invalid_requests_fail_before_preflight(self):
        invalid = [{"timeout": value} for value in (0, -1, True, float("inf"), float("nan"), 31)]
        invalid += [{"entry": "other.py"}, {"files": ["../main.py"]},
                    {"files": [".env"]}, {"files": ["main.py", "main.py"]},
                    {"files": ["secrets.json"]}]
        with patch.object(isolation, "check_isolation") as probe:
            for kwargs in invalid:
                with self.subTest(kwargs=kwargs), self.assertRaises(CollabError):
                    self.execute(**kwargs)
            probe.assert_not_called()

    def test_environment_is_allowlisted_not_inherited(self):
        with patch.dict(os.environ, {"WT_CANARY_SECRET": "secret", "PYTHONPATH": "hook",
                                     "DYLD_INSERT_LIBRARIES": "hook", "SSH_AUTH_SOCK": "socket",
                                     "OPENAI_API_KEY": "fixture", "HTTPS_PROXY": "credential"}):
            env = isolation._environment(self.root)
        self.assertEqual({"HOME", "TMPDIR", "PATH", "LANG", "LC_ALL"}, set(env))
        self.assertEqual(str(self.root), env["HOME"])
        self.assertNotIn("secret", env.values())

    def test_profile_has_no_general_process_network_or_host_write_grant(self):
        profile = isolation._profile()
        self.assertIn("(deny default)", profile)
        self.assertNotIn('(import "system.sb")', profile)
        for grant in ("(allow network", "(allow process-fork", "(allow mach-lookup"):
            self.assertNotIn(grant, profile)
        self.assertIn('(allow file-read* file-write* (subpath (param "WORK")))', profile)
        self.assertIn('(allow process-exec (literal (param "PYTHON")))', profile)

    def test_snapshots_preserve_exact_bytes_and_exclude_unselected_files(self):
        self.source.write_bytes("print('你好')\n".encode())
        (self.project / "other.py").write_text("must not copy")
        result = isolation._snapshot(self.project, self.db, ["main.py"])
        self.assertEqual({"main.py": self.source.read_bytes()}, result)

    def test_snapshot_rejects_links_state_binary_and_oversized_files(self):
        outside = self.root / "outside.py"
        outside.write_text("fixture")
        (self.project / "symlink.py").symlink_to(outside)
        os.link(outside, self.project / "hardlink.py")
        (self.project / "linked").symlink_to(self.root, target_is_directory=True)
        self.db.parent.mkdir()
        (self.db.parent / "ordinary.py").write_text("state marker")
        (self.project / "invalid.py").write_bytes(b"\xff")
        (self.project / "binary.py").write_bytes(b"\0")
        (self.project / "large.py").write_bytes(b"x" * (isolation.MAX_FILE_BYTES + 1))
        for path in ("symlink.py", "hardlink.py", "linked/outside.py", "state/ordinary.py",
                     "invalid.py", "binary.py", "large.py", "missing.py"):
            with self.subTest(path=path), self.assertRaises(CollabError):
                isolation._snapshot(self.project, self.db, [path])
        self.assertEqual("fixture", outside.read_text())

    def test_snapshot_rejects_fifo_without_blocking(self):
        os.mkfifo(self.project / "pipe.py")
        with self.assertRaises(CollabError):
            isolation._snapshot(self.project, self.db, ["pipe.py"])

    def test_payload_uses_copy_only_and_temporary_files_are_removed(self):
        workspaces = []
        def fake_launch(work, code, args, timeout):
            workspaces.append(work)
            self.assertNotEqual(self.project, work)
            self.assertEqual(["main.py"], args)
            self.assertEqual(self.source.read_bytes(), (work / "main.py").read_bytes())
            (work / "main.py").write_text("changed copy")
            (work / "new.py").write_text("generated")
            return {"status": "COMPLETED", "returncode": 0}
        with patch.object(isolation, "check_isolation", return_value={"status": "READY"}), \
                patch.object(isolation, "_launch", side_effect=fake_launch):
            result = self.execute()
        self.assertEqual(hashlib.sha256(self.source.read_bytes()).hexdigest(), result["input_sha256"]["main.py"])
        self.assertFalse(result["outputs_copied_back"])
        self.assertEqual("print('fixture')\n", self.source.read_text())
        self.assertFalse((self.project / "new.py").exists())
        self.assertFalse(workspaces[0].exists())

    def test_launch_failure_does_not_retry_and_cleans_copy(self):
        workspaces = []
        def fail(work, *args):
            workspaces.append(work)
            raise OSError("fixture")
        with patch.object(isolation, "check_isolation", return_value={"status": "READY"}), \
                patch.object(isolation, "_launch", side_effect=fail) as launch:
            with self.assertRaises(CollabError) as raised:
                self.execute()
        self.assertEqual("ISOLATION_UNAVAILABLE", raised.exception.code)
        self.assertEqual(1, launch.call_count)
        self.assertFalse(workspaces[0].exists())

    def test_project_cannot_overlap_readable_runtime_or_scratch_roots(self):
        with patch.object(isolation, "check_isolation", return_value={"status": "READY"}), \
                patch.object(isolation, "_snapshot") as snapshot:
            with patch.object(isolation.sys, "base_prefix", str(self.project)), self.assertRaises(CollabError) as error:
                self.execute()
            self.assertEqual("ISOLATION_SCOPE_DENIED", error.exception.code)
            with patch.object(isolation.tempfile, "gettempdir", return_value=str(self.project)), \
                    self.assertRaises(CollabError) as error:
                self.execute()
            self.assertEqual("ISOLATION_SCOPE_DENIED", error.exception.code)
            snapshot.assert_not_called()

    def test_probe_rejects_false_missing_malformed_or_nonzero_results(self):
        good = dict.fromkeys(isolation.PROBE_CHECKS, True)
        results = [{"status": "COMPLETED", "stdout": json.dumps({**good, "outside_read": False})},
                   {"status": "COMPLETED", "stdout": "{}"},
                   {"status": "COMPLETED", "stdout": "not json"},
                   {"status": "COMPLETED", "stdout": "[]"},
                   {"status": "FAILED", "stdout": json.dumps(good)}]
        with patch.object(isolation, "_runtime", return_value=(Path(sys.executable), self.root)):
            for result in results:
                with self.subTest(result=result), patch.object(isolation, "_launch", return_value={
                        **result, "returncode": 1, "stderr": "fixture"}):
                    self.assertEqual("BLOCKED", isolation.check_isolation()["status"])

    def test_cli_never_initializes_real_database(self):
        argv = ["--db", str(self.db), "isolation-check"]
        with patch("worktogether.__main__.Engine") as engine, \
                patch.object(isolation, "check_isolation", return_value={"status": "BLOCKED"}):
            self.assertEqual("BLOCKED", run(parser().parse_args(argv))["status"])
        engine.assert_not_called()
        self.assertFalse(self.db.parent.exists())

    def test_cli_blocked_is_nonzero_and_python_command_keeps_explicit_files(self):
        with patch.object(sys, "argv", ["worktogether", "isolation-check"]), \
                patch.object(isolation, "check_isolation", return_value={"status": "BLOCKED"}), \
                contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(3, main())
        args = parser().parse_args(["isolated-python", "--project-path", str(self.project),
                                  "--file", "main.py", "--file", "helper.py", "--entry", "main.py"])
        with patch("worktogether.__main__.Engine") as engine, \
                patch.object(isolation, "run_isolated_python", return_value={}) as execute:
            run(args)
        engine.assert_not_called()
        self.assertEqual(["main.py", "helper.py"], execute.call_args.args[1])


class BoundedCaptureTests(unittest.TestCase):
    def capture(self, code, timeout=3):
        # Fixed test programs only; exercises the pipe/time limiter independent
        # of native sandbox availability. Production goes through _launch.
        with tempfile.TemporaryDirectory() as temp:
            return isolation._capture([sys.executable, "-I", "-S", "-c", code],
                                      temp, isolation._environment(Path(temp)), timeout)

    def test_success_and_nonzero_exit_are_distinct(self):
        self.assertEqual("COMPLETED", self.capture("print('ok')")["status"])
        result = self.capture("import sys; print('failure', file=sys.stderr); sys.exit(7)")
        self.assertEqual(7, result["returncode"])
        self.assertEqual("NONZERO_EXIT", result["reason"])
        self.assertIn("failure", result["stderr"])

    def test_timeout_stops_and_reaps(self):
        result = self.capture("import time; time.sleep(20)", timeout=0.15)
        self.assertEqual("TIMEOUT", result["reason"])
        self.assertLess(result["returncode"], 0)

    def test_stdout_and_stderr_share_a_hard_byte_cap(self):
        result = self.capture("import os\nwhile True:\n os.write(1, b'x' * 8192)\n os.write(2, b'y' * 8192)")
        self.assertEqual("OUTPUT_LIMIT", result["reason"])
        self.assertEqual(isolation.MAX_OUTPUT, len(result["stdout"]) + len(result["stderr"]))

    def test_closed_pipes_do_not_escape_wall_clock_timeout(self):
        result = self.capture("import os,time; os.close(1); os.close(2); time.sleep(20)", timeout=0.15)
        self.assertEqual("TIMEOUT", result["reason"])


class NativeIsolationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        probe = isolation.check_isolation()
        if probe["status"] != "READY":
            if os.environ.get("WT_REQUIRE_ISOLATION_TESTS") == "1":
                raise AssertionError("Native isolation is required: " + json.dumps(probe))
            if sys.platform != "darwin" or "sandbox_apply: Operation not permitted" in probe.get("diagnostic", ""):
                raise unittest.SkipTest("Native macOS sandbox unavailable in this test environment")
            raise AssertionError("Unexpected native isolation failure: " + json.dumps(probe))

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="wt-isolation-test-")
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name).resolve()
        self.db = self.root / "state.sqlite3"

    def execute(self, code, timeout=5):
        (self.root / "main.py").write_text(code, encoding="utf-8")
        return isolation.run_isolated_python(self.root, ["main.py"], "main.py", state_db=self.db, timeout=timeout)

    def test_real_canaries_all_enforced(self):
        with patch.dict(os.environ, {"WT_CANARY_SECRET": "fixture-canary"}):
            result = isolation.check_isolation()
        self.assertEqual("READY", result["status"], result)
        self.assertEqual(dict.fromkeys(isolation.PROBE_CHECKS, True), result["checks"])

    def test_cli_end_to_end_runs_fixture_without_creating_database(self):
        (self.root / "main.py").write_text("print('offline fixture')\n")
        result = subprocess.run([sys.executable, "-m", "worktogether", "--db", str(self.db),
                                 "isolated-python", "--project-path", str(self.root),
                                 "--file", "main.py", "--entry", "main.py"],
                                capture_output=True, text=True, timeout=15)
        self.assertEqual(0, result.returncode, result.stderr + result.stdout)
        report = json.loads(result.stdout)
        self.assertEqual("COMPLETED", report["status"])
        self.assertEqual("offline fixture\n", report["stdout"])
        self.assertFalse(self.db.exists())

    def test_actual_test_runs_in_copy_without_secrets_or_modifying_source(self):
        code = """import os, unittest
from pathlib import Path
class Fixture(unittest.TestCase):
    def test_copy(self):
        self.assertNotIn('WT_CANARY_SECRET', os.environ)
        self.assertFalse(Path('unselected.txt').exists())
        Path('main.py').write_text('changed inside sandbox')
        Path('result.txt').write_text('sandbox only')
unittest.main()
"""
        (self.root / "unselected.txt").write_text("fixture")
        with patch.dict(os.environ, {"WT_CANARY_SECRET": "fixture"}):
            result = self.execute(code)
        self.assertEqual("COMPLETED", result["status"], result)
        self.assertIn("OK", result["stderr"])
        self.assertEqual(code, (self.root / "main.py").read_text())
        self.assertFalse((self.root / "result.txt").exists())
        self.assertFalse(self.db.exists())

    def test_original_project_cannot_be_read_or_written_and_limits_apply(self):
        canary = self.root / "original.txt"
        canary.write_text("unchanged")
        code = f'''import errno, resource
from pathlib import Path
p = Path({str(canary)!r})
for action in [p.read_text, lambda: p.write_text("changed")]:
    try: action()
    except OSError as exc: assert exc.errno in (errno.EACCES, errno.EPERM)
    else: raise AssertionError("original project was accessible")
assert resource.getrlimit(resource.RLIMIT_CORE) == (0, 0)
assert resource.getrlimit(resource.RLIMIT_FSIZE) == (1048576, 1048576)
assert resource.getrlimit(resource.RLIMIT_NOFILE) == (64, 64)
print("guarded")
'''
        result = self.execute(code)
        self.assertEqual("COMPLETED", result["status"], result)
        self.assertEqual("unchanged", canary.read_text())

    def test_real_timeout_and_output_limit(self):
        self.assertEqual("TIMEOUT", self.execute("while True: pass", timeout=0.2)["reason"])
        result = self.execute("import os\nwhile True: os.write(1, b'x' * 8192)")
        self.assertEqual("OUTPUT_LIMIT", result["reason"])

    def test_inheritable_parent_descriptor_is_not_passed_to_project_code(self):
        canary = self.root / "descriptor.txt"
        canary.write_text("fixture secret")
        with canary.open("rb") as stream:
            fd = fcntl.fcntl(stream.fileno(), fcntl.F_DUPFD, 48)
            try:
                os.set_inheritable(fd, True)
                result = self.execute(f'''import errno, os
try: os.read({fd}, 32)
except OSError as exc: assert exc.errno == errno.EBADF
else: raise AssertionError("parent descriptor leaked")
''')
            finally:
                os.close(fd)
        self.assertEqual("COMPLETED", result["status"], result)

    def test_file_size_limit_is_enforced_and_hard_limits_cannot_be_raised(self):
        result = self.execute('''import errno, os, resource
try: resource.setrlimit(resource.RLIMIT_NOFILE, (128, 128))
except (ValueError, OSError): pass
else: raise AssertionError("hard limit increased")
with open("bounded", "wb", buffering=0) as stream:
    stream.write(b"x" * 1048576)
    try: stream.write(b"x")
    except OSError as exc: assert exc.errno == errno.EFBIG
    else: raise AssertionError("file size limit bypassed")
assert os.stat("bounded").st_size == 1048576
''')
        self.assertEqual("COMPLETED", result["status"], result)
