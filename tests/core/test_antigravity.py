import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from worktogether.core import Engine, CollabError
from worktogether.adapters import MockAdapter
from worktogether.antigravity import AntigravityRuntime


class AntigravityTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.e = Engine(Path(self.tmp.name) / "state.sqlite3")
        p = self.e.project_add("fixture", self.tmp.name)
        self.task = self.e.task_add(p["id"], "fixture", "fixture")["id"]
        d = self.e.dispatch(self.task)["dispatch"]
        self.e.submit(self.task, MockAdapter().prepare(d, self.e.template(self.task, d["id"])))
        self.d = self.e.dispatch(self.task)["dispatch"]
        self.r = AntigravityRuntime(self.e, str(Path(__file__).with_name("fake_agy.py").resolve()))

    def test_review_to_codex_finalization(self):
        run = self.r.run(self.task, self.d["id"])
        self.assertEqual("COMPLETED", run["status"], run)
        next_dispatch = self.e.dispatch(self.task)["dispatch"]
        self.assertEqual("codex", next_dispatch["role"])
        self.assertEqual("PLAN_FINALIZE", next_dispatch["kind"])
        self.assertEqual(["Check persistence"], next_dispatch["payload"]["plan_review"]["suggestions"])
        self.assertEqual(run, self.r.collect(run["id"]))

    def test_waiting_mismatch_duplicate_fail_closed(self):
        for mode in ("waiting", "mismatch", "duplicate"):
            with self.subTest(mode=mode), patch.dict(os.environ, {"WT_AGY_FIXTURE": mode}):
                run = self.r.run(self.task, self.d["id"])
                self.assertEqual("FAILED", run["status"], run)
                self.assertEqual("BLOCKED", self.e.show(self.task)["status"])
                self.d = self.e.retry(self.task, self.d["id"])["dispatch"]

    def test_explicit_resume_no_latest(self):
        run, d = self.r._reserve(self.task, self.d["id"], 30)
        run["session_id"] = "known-id"
        args = self.r.command(run, d)
        self.assertIn("--conversation", args)
        self.assertIn("known-id", args)
        self.assertNotIn("--continue", args)
        self.assertNotIn("--dangerously-skip-permissions", args)

    def test_wrong_role_rejected(self):
        self.r.run(self.task, self.d["id"])
        d = self.e.dispatch(self.task)["dispatch"]
        with self.assertRaises(CollabError):
            self.r.run(self.task, d["id"])
