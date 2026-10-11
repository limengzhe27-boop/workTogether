"""Tests for attributing file changes to the agent turn that made them."""
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from worktogether.antigravity import AntigravityRuntime
from worktogether.core import Engine
from worktogether.runtime import Runtime
from worktogether.workspace import changed_files, file_digests


class ChangeAttributionTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.project = Path(self.tmp.name) / "project"
        self.project.mkdir()
        (self.project / "README.md").write_text("# Room fixture\n", encoding="utf-8")
        (self.project / "app.py").write_text("x = 1\n", encoding="utf-8")
        self.e = Engine(str(Path(self.tmp.name) / "state.sqlite3"))
        project = self.e.project_add("fixture", str(self.project))
        self.project_id = project["id"]
        self.task = self.e.task_add(self.project_id, "room", "change it")["id"]
        root = Path(__file__).parent
        self.codex = Runtime(self.e, str((root / "fake_codex.py").resolve()))
        self.agy = AntigravityRuntime(self.e, str((root / "fake_agy.py").resolve()))

    def test_digests_detect_added_modified_and_removed_files(self):
        before = file_digests(self.project)
        (self.project / "README.md").write_text("# changed\n", encoding="utf-8")
        (self.project / "new.txt").write_text("new\n", encoding="utf-8")
        (self.project / "app.py").unlink()
        self.assertEqual(
            [{"path": "README.md", "status": "modified"},
             {"path": "app.py", "status": "removed"},
             {"path": "new.txt", "status": "added"}],
            changed_files(before, file_digests(self.project)))

    def test_a_write_turn_reports_the_files_it_touched(self):
        self.e.message_add(self.task, "m", "codex", "改一下 README")
        with patch.dict("os.environ", {"WT_FAKE_MODE": "write"}):
            dispatch = self.e.chat_dispatch_round(self.task)[0]
            self.assertTrue(dispatch["payload"].get("allow_write"))
            run = self.codex.run(self.task, dispatch["id"], 30)
        self.assertEqual("COMPLETED", run["status"], run)
        self.assertEqual(
            [{"path": "README.md", "status": "modified"},
             {"path": "fixture-wrote.txt", "status": "added"}],
            run["changed_files"])
        # The reply carries the same attribution for the room to render.
        reply = self.e.messages(self.task)[0]["replies"][0]
        self.assertEqual(run["changed_files"], reply["changed_files"])

    def test_a_read_only_turn_that_still_wrote_is_reported(self):
        self.e.chat_config(self.task, {"allow_write": False})
        self.e.message_add(self.task, "m", "codex", "只是讨论")
        with patch.dict("os.environ", {"WT_FAKE_MODE": "write"}):
            dispatch = self.e.chat_dispatch_round(self.task)[0]
            self.assertFalse(dispatch["payload"].get("allow_write"))
            run = self.codex.run(self.task, dispatch["id"], 30)
        self.assertEqual("COMPLETED", run["status"], run)
        # A write without the grant must surface, not pass unnoticed.
        self.assertEqual(
            [{"path": "README.md", "status": "modified"},
             {"path": "fixture-wrote.txt", "status": "added"}],
            run["changed_files"])
        events = []
        with self.e.transaction() as db:
            events = [r[0] for r in db.execute("SELECT kind FROM events")]
        self.assertIn("WORKSPACE_CHANGED_BY_AGENT", events)

    def test_a_turn_that_changes_nothing_reports_an_empty_list(self):
        self.e.message_add(self.task, "m", "codex", "只是看看")
        dispatch = self.e.chat_dispatch_round(self.task)[0]
        run = self.codex.run(self.task, dispatch["id"], 30)
        self.assertEqual("COMPLETED", run["status"], run)
        self.assertEqual([], run["changed_files"])
        events = []
        with self.e.transaction() as db:
            events = [r[0] for r in db.execute("SELECT kind FROM events")]
        # No noise in the audit trail when nothing changed.
        self.assertNotIn("WORKSPACE_CHANGED_BY_AGENT", events)


if __name__ == "__main__":
    unittest.main()
