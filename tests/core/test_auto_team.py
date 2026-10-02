import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from worktogether.auto_team import TeamJobs
from worktogether.core import CollabError, Engine
from worktogether.runtime import Runtime
from worktogether.antigravity import AntigravityRuntime
from worktogether.jobs import PlanningJobs
from worktogether.teamwork import discuss_team
from tests.core.test_server import DummyServer, execute_request


class AutoTeamTests(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.engine = Engine(Path(tmp.name) / "state.db")
        project = self.engine.project_add("Project", tmp.name)
        self.task = self.engine.task_add(project["id"], "Task", "Implement feature")["id"]
        root = Path(__file__).parent
        self.codex = Runtime(self.engine, str(root / "fake_codex.py"))
        self.codex.require_approval = True
        self.agy = AntigravityRuntime(self.engine, str(root / "fake_agy.py"))
        self.engine.configure_team(self.task, ["codex", "antigravity"])
        self.jobs = TeamJobs(self.engine, self.codex, self.agy)

    def queued(self):
        with patch("threading.Thread.start"):
            return self.jobs.start(self.task)

    def test_explicit_delegation_completes_with_bounded_budget(self):
        job = self.queued()
        self.assertEqual(19, job["max_calls"])
        self.jobs._work(job, 30)
        result = self.jobs.list(self.task)[0]
        self.assertEqual("COMPLETED", result["result"]["reason"])
        self.assertEqual(9, len(result["result"]["runs"]))
        self.assertEqual("COMPLETED", self.engine.show(self.task)["status"])
        events = self.engine.show(self.task)["events"]
        self.assertEqual(1, sum(e["kind"] == "DELEGATED_PLAN_APPROVED" for e in events))
        self.jobs._work(job, 30)
        self.assertEqual(9, len(self.codex.list(self.task)["runs"]))

    def test_other_controllers_and_revision_cannot_steal_queued_work(self):
        self.queued()
        for action in (lambda: self.jobs.start(self.task),
                       lambda: PlanningJobs(self.engine, self.codex, self.agy).start(self.task),
                       lambda: self.engine.revise(self.task, "r", 1, "Different"),
                       lambda: self.engine.configure_team(self.task, ["codex", "antigravity"])):
            with self.assertRaises(CollabError):
                action()

    def test_pause_after_plan_does_not_auto_approve_or_execute(self):
        job = self.queued()
        def planning(*args):
            result = discuss_team(*args)
            self.engine.pause(self.task)
            return result
        with patch("worktogether.auto_team.discuss_team", side_effect=planning):
            result = self.jobs.perform_job(job, 30)
        self.assertEqual("TASK_NOT_ACTIVE", result["reason"])
        self.assertFalse(self.codex.approval(self.task)["approved"])
        self.assertEqual(7, len(self.codex.list(self.task)["runs"]))

    def test_budget_exhaustion_is_not_task_completion(self):
        job = self.queued()
        job["max_calls"] = 8
        result = self.jobs.perform_job(job, 30)
        self.assertEqual("CALL_LIMIT", result["reason"])
        self.assertEqual(8, len(result["runs"]))
        self.assertEqual("ACTIVE", self.engine.show(self.task)["status"])

    def test_changed_authorization_does_not_approve(self):
        job = self.queued()
        job["authorization"]["revision"] += 1
        with self.assertRaises(CollabError):
            self.jobs.perform_job(job, 30)
        self.assertFalse(self.codex.approval(self.task)["approved"])

    def test_http_requires_explicit_boolean_authorization(self):
        server = DummyServer(self.engine, self.codex, ".")
        server.team_jobs = self.jobs
        url = "/api/tasks/" + self.task
        for body in ({}, {"authorize_execution": False}, {"authorize_execution": 1}):
            status, _ = execute_request(server, "POST", url + "/delegate-team", body)
            self.assertNotEqual(202, status)
        with patch("threading.Thread.start"):
            status, job = execute_request(server, "POST", url + "/delegate-team", {"authorize_execution": True})
        self.assertEqual(202, status)
        self.assertEqual(19, job["max_calls"])
        status, result = execute_request(server, "GET", url + "/team-jobs")
        self.assertEqual(200, status)
        self.assertEqual(job["id"], result["jobs"][0]["id"])

    def test_new_revision_can_reconfigure_and_delegate_again(self):
        job = self.queued()
        self.jobs._work(job, 30)
        self.engine.message_add(self.task, "old", "all", "Old follow-up")
        self.engine.revise(self.task, "r", 1, "Refined requirement")
        self.assertEqual("NEEDS_REVIEW", self.engine.messages(self.task)[0]["status"])
        self.engine.configure_team(self.task, ["codex", "antigravity"], "codex", "antigravity")
        next_job = self.queued()
        self.assertEqual(2, next_job["authorization"]["revision"])
        self.jobs._work(next_job, 30)
        self.assertEqual("COMPLETED", self.engine.show(self.task)["status"])
        self.assertEqual(18, len(self.codex.list(self.task)["runs"]))
