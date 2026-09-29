import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from worktogether.adapters import MockAdapter
from worktogether.core import CollabError, Engine
from worktogether.runtime import Runtime


class RuntimeTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.e = Engine(Path(self.tmp.name) / "state.sqlite3")
        p = self.e.project_add("fixture", self.tmp.name)
        self.t = self.e.task_add(p["id"], "test", "test")["id"]
        self.d = self.e.dispatch(self.t)["dispatch"]
        fixture = Path(__file__).with_name("fake_codex.py").resolve()
        self.r = Runtime(self.e, str(fixture))

    def test_real_subprocess_collection_and_exact_session_resume(self):
        result = self.r.run(self.t, self.d["id"])
        self.assertEqual("COMPLETED", result["status"], result)
        self.assertEqual(10, result["usage"]["input_tokens"])
        self.assertEqual(result, self.r.collect(result["id"]))
        review = self.e.dispatch(self.t)["dispatch"]
        self.e.submit(self.t, MockAdapter().prepare(review, self.e.template(self.t, review["id"])))
        final = self.e.dispatch(self.t)["dispatch"]
        next_run = self.r.run(self.t, final["id"])
        self.assertEqual("COMPLETED", next_run["status"], next_run)
        self.assertEqual(result["session_id"], next_run["session_id"])
        command = (Path(next_run["artifact_dir"]) / "command.json").read_text()
        self.assertIn("resume", command)
        self.assertNotIn("--last", command)

    def test_launch_not_repeated(self):
        self.r._reserve(self.t, self.d["id"], 30)
        with self.assertRaises(CollabError) as c:
            self.r.run(self.t, self.d["id"])
        self.assertEqual("ALREADY_STARTED", c.exception.code)

    def test_wrong_resumed_session_rejected(self):
        first = self.r.run(self.t, self.d["id"])
        review = self.e.dispatch(self.t)["dispatch"]
        self.e.submit(self.t, MockAdapter().prepare(review, self.e.template(self.t, review["id"])))
        final = self.e.dispatch(self.t)["dispatch"]
        with patch.dict(os.environ, {"WT_FAKE_MODE": "wrong-session"}):
            result = self.r.run(self.t, final["id"])
        self.assertEqual("FAILED", result["status"])
        self.assertIn("session mismatch", result["error"])
        self.assertEqual("BLOCKED", self.e.show(self.t)["status"])

    def test_collect_recovers_after_ingestion_before_run_commit(self):
        result = self.r.run(self.t, self.d["id"])
        count = len(self.e.show(self.t)["events"])
        with self.e.transaction() as db:
            result["status"] = "EXITED"
            self.r._save(db, result)
        recovered = Runtime(Engine(self.e.path), self.r.executable).collect(result["id"])
        self.assertEqual("COMPLETED", recovered["status"])
        self.assertEqual(count, len(self.e.show(self.t)["events"]))

    def test_timeout_and_nonzero_block(self):
        for mode in ("timeout", "exit", "invalid"):
            with self.subTest(mode=mode), patch.dict(os.environ, {"WT_FAKE_MODE": mode}):
                result = self.r.run(self.t, self.d["id"], timeout=0.2)
                self.assertEqual("FAILED", result["status"])
                self.assertEqual("BLOCKED", self.e.show(self.t)["status"])
                self.d = self.e.retry(self.t, self.d["id"])["dispatch"]

    def test_orphan_needs_explicit_resolution(self):
        run, _ = self.r._reserve(self.t, self.d["id"], 30)
        with self.assertRaises(CollabError):
            self.r.resolve_stopped(run["id"])
        with self.assertRaises(CollabError):
            self.e.submit(self.t, MockAdapter().prepare(self.d, self.e.template(self.t, self.d["id"])))
        self.r.resolve_stopped(run["id"], True)
        retried = self.e.retry(self.t, self.d["id"])["dispatch"]
        self.assertEqual(2, retried["attempt"])

    def test_missing_binary(self):
        self.r.executable = "/definitely/missing/codex"
        result = self.r.run(self.t, self.d["id"])
        self.assertEqual("FAILED", result["status"])
        self.assertEqual("BLOCKED", self.e.show(self.t)["status"])

    def test_pause_prevents_launch(self):
        self.e.pause(self.t)
        with self.assertRaises(CollabError):
            self.r.run(self.t, self.d["id"])
        self.assertEqual([], self.r.list(self.t)["runs"])


if __name__ == "__main__":
    unittest.main()
