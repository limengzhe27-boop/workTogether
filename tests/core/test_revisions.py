import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from worktogether.core import Engine, CollabError
from worktogether.runtime import Runtime
from worktogether.jobs import PlanningJobs
from worktogether.antigravity import AntigravityRuntime
from worktogether.adapters import MockAdapter
from tests.core.test_server import DummyServer, execute_request


class RevisionTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.e = Engine(Path(self.tmp.name) / "state.sqlite3")
        self.r = Runtime(self.e)
        p = self.e.project_add("p", self.tmp.name)
        self.t = self.e.task_add(p["id"], "task", "original")["id"]

    def plan(self):
        for _ in range(3):
            d = self.e.dispatch(self.t)["dispatch"]
            self.e.submit(self.t, MockAdapter().prepare(d, self.e.template(self.t, d["id"])))

    def test_revision_archives_and_revokes_approval(self):
        self.plan()
        old = self.e.show(self.t)
        self.r.approval(self.t, self.r.plan_digest(old))
        self.e.revise(self.t, "message-1", 1, "revised")
        task = self.e.show(self.t)
        self.assertEqual("PLANNING", task["phase"])
        self.assertEqual(2, task["revision"])
        self.assertEqual(old["steps"], task["revisions"][0]["previous_steps"])
        self.assertIsNone(task["draft_plan"])
        self.assertFalse(self.r.approval(self.t)["approved"])
        d = self.e.dispatch(self.t)["dispatch"]
        self.assertEqual(2, d["payload"]["requirement_revision"])
        self.assertEqual("revised", d["payload"]["requirement"])

    def test_idempotent_replay_and_conflicting_revision(self):
        self.e.revise(self.t, "m", 1, "new")
        events = self.e.show(self.t)["events"]
        self.assertTrue(self.e.revise(self.t, "m", 1, "new")["duplicate"])
        self.assertEqual(events, self.e.show(self.t)["events"])
        for message, rev, content in (("m", 1, "different"), ("other", 1, "new")):
            with self.assertRaises(CollabError) as error:
                self.e.revise(self.t, message, rev, content)
            self.assertEqual("CONFLICT", error.exception.code)

    def test_active_dispatch_and_unknown_run_rejected(self):
        d = self.e.dispatch(self.t)["dispatch"]
        with self.assertRaises(CollabError):
            self.e.revise(self.t, "m", 1, "new")
        self.r._reserve(self.t, d["id"], 30)
        with self.e.transaction() as db:
            t = self.e._task(db, self.t)
            t["dispatches"][0]["status"] = "BLOCKED"
            self.e._save(db, t, "TEST_TIMEOUT", {})
        with self.assertRaises(CollabError):
            self.e.revise(self.t, "m", 1, "new")

    def test_queued_job_prevents_revision(self):
        jobs = PlanningJobs(self.e, self.r, AntigravityRuntime(self.e))
        with patch("worktogether.jobs.threading.Thread.start"):
            jobs.start(self.t)
        with self.assertRaises(CollabError):
            self.e.revise(self.t, "m", 1, "new")

    def test_paused_stays_paused_and_old_report_does_not_change_plan(self):
        self.plan()
        old = self.e.show(self.t)["dispatches"][0]["report"]
        self.e.pause(self.t)
        self.e.revise(self.t, "m", 1, "new")
        self.assertTrue(self.e.submit(self.t, old)["duplicate"])
        task = self.e.show(self.t)
        self.assertEqual("PAUSED", task["status"])
        self.assertIsNone(task["draft_plan"])
        self.assertIsNone(self.e.dispatch(self.t)["dispatch"])

    def test_http_and_isolation(self):
        server = DummyServer(self.e, self.r, self.tmp.name)
        status, result = execute_request(server, "POST", f"/api/tasks/{self.t}/revisions",
            {"message_id": "m", "expected_revision": 1, "requirement": "updated"})
        self.assertEqual(200, status)
        self.assertEqual(2, result["revision"])
        self.assertEqual(1, len(self.e.tasks_list()))
        status, _ = execute_request(server, "POST", "/api/tasks/nonexistent/revisions",
            {"message_id": "m", "expected_revision": 1, "requirement": "updated"})
        self.assertEqual(404, status)
