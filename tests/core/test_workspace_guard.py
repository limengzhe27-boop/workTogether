import os
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from worktogether.adapters import MockAdapter
from worktogether.antigravity import AntigravityRuntime
from worktogether.core import Engine, encode
from worktogether.runtime import Runtime
from worktogether.workspace import fingerprint


class WorkspaceGuardTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.project = Path(self.tmp.name) / "project"
        self.project.mkdir()
        subprocess.run(["git", "init", "-q", str(self.project)], check=True)
        (self.project / "tracked.txt").write_text("original", encoding="utf-8")
        subprocess.run(["git", "add", "tracked.txt"], cwd=self.project, check=True)
        self.state = self.project / ".collab" / "state.sqlite3"
        self.engine = Engine(self.state)
        self.runtime = AntigravityRuntime(self.engine, str(Path(__file__).with_name("fake_agy.py")))
        project = self.engine.project_add("fixture", str(self.project))
        self.task = self.engine.task_add(project["id"], "fixture", "Inspect tracked file")["id"]

    def prepare_verification(self):
        for _ in range(3):
            d = self.engine.dispatch(self.task)["dispatch"]
            report = MockAdapter().prepare(d, self.engine.template(self.task, d["id"]))
            self.engine.submit(self.task, report)
        code = self.engine.dispatch(self.task)["dispatch"]
        self.engine.submit(self.task, MockAdapter().prepare(
            code, self.engine.template(self.task, code["id"])))
        digest = fingerprint(self.project, self.state)
        # Model run metadata is the authority for automatic handoff provenance.
        with self.engine.transaction() as db:
            db.execute("INSERT INTO runs VALUES(?,?,?,?)",
                       ("code-run", self.task, code["id"], encode({"workspace_fingerprint": digest})))
        return self.engine.dispatch(self.task)["dispatch"]

    def test_fingerprint_detects_code_drift_but_ignores_platform_state(self):
        first = fingerprint(self.project, self.state)
        self.assertEqual(first, fingerprint(self.project, self.state))
        (self.state.parent / "artifact.txt").write_text("platform", encoding="utf-8")
        self.assertEqual(first, fingerprint(self.project, self.state))
        (self.project / "untracked.txt").write_text("new", encoding="utf-8")
        self.assertNotEqual(first, fingerprint(self.project, self.state))
        (self.project / "untracked.txt").unlink()
        (self.project / "tracked.txt").write_text("changed", encoding="utf-8")
        self.assertNotEqual(first, fingerprint(self.project, self.state))

    def test_changed_workspace_blocks_verification_before_agent_launch(self):
        verify = self.prepare_verification()
        (self.project / "tracked.txt").write_text("changed", encoding="utf-8")
        run = self.runtime.run(self.task, verify["id"])
        self.assertEqual("FAILED", run["status"])
        self.assertEqual("WORKSPACE_CHANGED", run["error_code"])
        self.assertFalse(Path(run["artifact_dir"]).exists())
        self.assertEqual("WORKSPACE_CHANGED", self.engine.show(self.task)["block_reason"])

    def test_unchanged_workspace_can_pass_verification(self):
        verify = self.prepare_verification()
        run = self.runtime.run(self.task, verify["id"])
        self.assertEqual("COMPLETED", run["status"], run)
        self.assertEqual("COMPLETED", self.engine.show(self.task)["status"])

    def test_automatic_code_run_records_handoff_digest(self):
        for _ in range(3):
            d = self.engine.dispatch(self.task)["dispatch"]
            self.engine.submit(self.task, MockAdapter().prepare(
                d, self.engine.template(self.task, d["id"])))
        code = self.engine.dispatch(self.task)["dispatch"]
        codex = Runtime(self.engine, str(Path(__file__).with_name("fake_codex.py")))
        run = codex.run(self.task, code["id"])
        self.assertEqual("COMPLETED", run["status"], run)
        self.assertEqual(fingerprint(self.project, self.state), run["workspace_fingerprint"])
        verify = self.engine.dispatch(self.task)["dispatch"]
        self.assertEqual("COMPLETED", self.runtime.run(self.task, verify["id"])["status"])

    def test_agent_edit_during_verification_is_not_accepted(self):
        verify = self.prepare_verification()
        with patch.dict(os.environ, {"WT_AGY_FIXTURE": "mutate"}):
            run = self.runtime.run(self.task, verify["id"])
        self.assertEqual("FAILED", run["status"])
        self.assertEqual("WORKSPACE_CHANGED", run["error_code"])
        self.assertIsNone(self.engine.show(self.task)["dispatches"][-1]["report"])


if __name__ == "__main__":
    unittest.main()
