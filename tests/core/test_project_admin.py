"""Tests for project memory, project/task administration and cascade deletion."""
import tempfile
import unittest
from pathlib import Path

from worktogether.core import CollabError, Engine, encode
from worktogether.runtime import Runtime


class ProjectAdminTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.project_dir = Path(self.tmp.name) / "project"
        self.project_dir.mkdir()
        self.engine = Engine(str(Path(self.tmp.name) / "state.sqlite3"))
        Runtime(self.engine)  # Creates the provider-independent runs table.
        self.project = self.engine.project_add("fixture", str(self.project_dir))
        self.task = self.engine.task_add(self.project["id"], "first", "do something")

    def test_project_memory_round_trips_through_doctor_and_list(self):
        self.engine.project_memory_update(self.project["id"], "  使用 Python 3.9，无第三方依赖  ")
        self.assertEqual("使用 Python 3.9，无第三方依赖",
                         self.engine.doctor(self.project["id"])["project"]["memory"])
        self.assertEqual("使用 Python 3.9，无第三方依赖",
                         self.engine.projects_list()[0]["memory"])

    def test_project_memory_is_bounded_and_typed(self):
        with self.assertRaises(CollabError):
            self.engine.project_memory_update(self.project["id"], "x" * 20001)
        with self.assertRaises(CollabError):
            self.engine.project_memory_update(self.project["id"], None)

    def test_unknown_project_memory_update_is_rejected(self):
        with self.assertRaises(CollabError) as ctx:
            self.engine.project_memory_update("missing", "note")
        self.assertEqual("NOT_FOUND", ctx.exception.code)

    def test_task_rename_validates_and_persists(self):
        renamed = self.engine.task_rename(self.task["id"], "  新标题  ")
        self.assertEqual("新标题", renamed["title"])
        self.assertEqual("新标题", self.engine.show(self.task["id"])["title"])
        with self.assertRaises(CollabError):
            self.engine.task_rename(self.task["id"], "   ")
        with self.assertRaises(CollabError):
            self.engine.task_rename(self.task["id"], "x" * 201)

    def test_task_delete_removes_history_rows(self):
        self.engine.task_rename(self.task["id"], "renamed")
        result = self.engine.task_delete(self.task["id"])
        self.assertEqual({"deleted": self.task["id"]}, result)
        with self.assertRaises(CollabError):
            self.engine.show(self.task["id"])
        with self.engine.transaction() as db:
            for table in ("messages", "events", "runs"):
                remaining = db.execute(f"SELECT COUNT(*) FROM {table} WHERE task_id=?", (self.task["id"],)).fetchone()[0]
                self.assertEqual(0, remaining, table)

    def test_task_delete_refuses_while_a_run_is_in_flight(self):
        with self.engine.transaction() as db:
            db.execute("INSERT INTO runs VALUES(?,?,?,?)",
                       ("run-1", self.task["id"], "dispatch-1", encode({"status": "RUNNING"})))
        with self.assertRaises(CollabError) as ctx:
            self.engine.task_delete(self.task["id"])
        self.assertEqual("INVALID_STATE", ctx.exception.code)
        self.assertEqual("first", self.engine.show(self.task["id"])["title"])

    def test_project_delete_cascades_tasks_and_memory(self):
        second = self.engine.task_add(self.project["id"], "second", "another")
        self.engine.project_memory_update(self.project["id"], "note")
        result = self.engine.project_delete(self.project["id"])
        self.assertEqual(2, result["tasks_removed"])
        self.assertEqual([], self.engine.projects_list())
        with self.assertRaises(CollabError):
            self.engine.show(second["id"])
        with self.engine.transaction() as db:
            self.assertEqual(0, db.execute("SELECT COUNT(*) FROM project_meta").fetchone()[0])

    def test_memory_is_scope_isolated_between_projects(self):
        other_dir = Path(self.tmp.name) / "other"
        other_dir.mkdir()
        other = self.engine.project_add("other", str(other_dir))
        self.engine.project_memory_update(self.project["id"], "only mine")
        self.assertEqual("", self.engine.doctor(other["id"])["project"].get("memory", ""))

    def test_events_record_memory_and_rename_actions(self):
        self.engine.project_memory_update(self.project["id"], "note")
        self.engine.task_rename(self.task["id"], "renamed")
        with self.engine.transaction() as db:
            kinds = [r[0] for r in db.execute("SELECT kind FROM events ORDER BY rowid")]
        self.assertIn("TASK_RENAMED", kinds)


if __name__ == "__main__":
    unittest.main()
