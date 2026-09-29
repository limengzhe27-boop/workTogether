import tempfile
import unittest
from pathlib import Path
from worktogether.core import Engine
from worktogether.runtime import Runtime
from worktogether.antigravity import AntigravityRuntime
from worktogether.discussion import discuss


class DiscussionTests(unittest.TestCase):
    def test_bounded_exchange_resumes_codex_and_never_codes(self):
        with tempfile.TemporaryDirectory() as tmp:
            e = Engine(Path(tmp) / "state.sqlite3")
            p = e.project_add("test", tmp)
            task = e.task_add(p["id"], "test", "test")["id"]
            root = Path(__file__).parent
            c = Runtime(e, str((root / "fake_codex.py").resolve()))
            a = AntigravityRuntime(e, str((root / "fake_agy.py").resolve()))
            result = discuss(e, task, c, a)
            self.assertEqual("STOPPED", result["status"])
            self.assertEqual(3, len(result["runs"]))
            self.assertTrue(all(r["status"] == "COMPLETED" for r in result["runs"]))
            self.assertEqual(result["runs"][0]["session_id"], result["runs"][2]["session_id"])
            self.assertEqual(["PLAN", "PLAN_REVIEW", "PLAN_FINALIZE"],
                             [d["kind"] for d in e.show(task)["dispatches"]])
            self.assertEqual([], discuss(e, task, c, a)["runs"])
