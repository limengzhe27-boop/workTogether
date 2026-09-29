import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from worktogether.core import Engine, CollabError
from worktogether.runtime import Runtime
from worktogether.antigravity import AntigravityRuntime
from worktogether.jobs import PlanningJobs
from worktogether.adapters import MockAdapter
from tests.core.test_server import DummyServer, execute_request


class WebPlanningTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.e = Engine(Path(self.tmp.name) / "state.sqlite3")
        root = Path(__file__).parent
        self.c = Runtime(self.e, str((root / "fake_codex.py").resolve()))
        self.c.require_approval = True
        self.a = AntigravityRuntime(self.e, str((root / "fake_agy.py").resolve()))
        self.jobs = PlanningJobs(self.e, self.c, self.a)
        self.s = DummyServer(self.e, self.c, self.tmp.name)
        self.s.planning_jobs = self.jobs
        self.s.antigravity_runtime = self.a
        p = self.e.project_add("test", self.tmp.name)
        self.task = self.e.task_add(p["id"], "test", "test")["id"]
        self.url = "/api/tasks/" + self.task

    def test_http_exchange_is_bounded_and_persisted(self):
        with patch("worktogether.jobs.threading.Thread.start"):
            status, job = execute_request(self.s, "POST", self.url + "/discuss", {})
            self.assertEqual(202, status)
            status, _ = execute_request(self.s, "POST", self.url + "/discuss", {})
            self.assertEqual(409, status)
        self.jobs._work(job, 30)
        status, data = execute_request(self.s, "GET", self.url + "/planning-jobs")
        self.assertEqual(200, status)
        self.assertEqual("COMPLETED", data["jobs"][0]["status"])
        self.assertEqual(3, len(self.c.list(self.task)["runs"]))
        self.assertFalse(any(d["kind"] == "CODE" for d in self.e.show(self.task)["dispatches"]))
        self.assertEqual(data["jobs"], PlanningJobs(self.e, self.c, self.a).list(self.task))

    def test_unknown_job_not_replayed_after_restart(self):
        with patch("worktogether.jobs.threading.Thread.start"):
            self.jobs.start(self.task)
        with self.assertRaises(CollabError):
            PlanningJobs(self.e, self.c, self.a).start(self.task)

    def test_recover_queued_job_prevents_delayed_worker(self):
        with patch("worktogether.jobs.threading.Thread.start"):
            job = self.jobs.start(self.task)
        status, recovered = execute_request(self.s, "POST", self.url + '/planning-jobs/' + job['id'] + '/recover', {})
        self.assertEqual(200, status)
        self.assertEqual('INTERRUPTED', recovered['status'])
        self.jobs._work(job, 30)
        self.assertEqual([], self.c.list(self.task)['runs'])
        self.assertEqual('INTERRUPTED', self.jobs.list(self.task)[0]['status'])
        self.assertEqual(recovered, self.jobs.recover(self.task, job['id']))

    def test_recover_rejects_live_lease(self):
        with patch("worktogether.jobs.threading.Thread.start"):
            job = self.jobs.start(self.task)
        with self.jobs._lease(job['id']):
            with self.assertRaises(CollabError):
                self.jobs.recover(self.task, job['id'])

    def test_recover_rejects_unresolved_dispatch_and_other_task(self):
        with patch("worktogether.jobs.threading.Thread.start"):
            job = self.jobs.start(self.task)
        d = self.e.dispatch(self.task)['dispatch']
        with self.assertRaises(CollabError):
            self.jobs.recover(self.task, job['id'])
        self.assertEqual('ACTIVE', d['status'])
        project = self.e.project_add('other', self.tmp.name)
        other = self.e.task_add(project['id'], 'other', 'other')['id']
        with self.assertRaises(CollabError):
            self.jobs.recover(other, job['id'])

    def test_external_origin_and_invalid_body_rejected(self):
        for method in ("GET", "POST"):
            status, _ = execute_request(self.s, method, "/api/projects", headers={"Origin": "https://evil.example"})
            self.assertEqual(403, status)
        status, _ = execute_request(self.s, "POST", self.url + "/discuss", [])
        self.assertEqual(400, status)
        status, _ = execute_request(self.s, "POST", self.url + "/discuss", {"executable": "/tmp/untrusted"})
        self.assertEqual(400, status)

    def test_approval_binds_requirement_and_plan(self):
        for _ in range(3):
            d = self.e.dispatch(self.task)["dispatch"]
            self.e.submit(self.task, MockAdapter().prepare(d, self.e.template(self.task, d["id"])))
        d = self.e.dispatch(self.task)["dispatch"]
        with self.assertRaises(CollabError) as error:
            self.c._reserve(self.task, d["id"], 30)
        self.assertEqual("APPROVAL_REQUIRED", error.exception.code)
        status, _ = execute_request(self.s, "POST", self.url + "/approve", {"digest": "stale"})
        self.assertEqual(409, status)
        _, approval = execute_request(self.s, "GET", self.url + "/approval")
        status, accepted = execute_request(self.s, "POST", self.url + "/approve", {"digest": approval["digest"]})
        self.assertEqual(200, status)
        self.assertTrue(accepted["approved"])
        with self.e.transaction() as db:
            task = self.e._task(db, self.task)
            task["requirement"] = "changed requirement"
            self.e._save(db, task, "TEST_REVISION", {})
        self.assertFalse(self.c.approval(self.task)["approved"])
        with self.assertRaises(CollabError):
            self.c._reserve(self.task, d["id"], 30)

    def test_run_reserves_before_accepting_and_rejects_repeat(self):
        d = self.e.dispatch(self.task)["dispatch"]
        with patch("worktogether.server.threading.Thread.start"):
            status, result = execute_request(self.s, "POST", self.url + "/run", {"dispatch_id": d["id"]})
            self.assertEqual(202, status)
            self.assertIn("run_id", result)
            status, _ = execute_request(self.s, "POST", self.url + "/run", {"dispatch_id": d["id"]})
            self.assertEqual(409, status)
        self.assertEqual("PREPARED", self.c.list(self.task)["runs"][0]["status"])

    def test_provider_failure_is_visible(self):
        self.jobs.antigravity.executable = "/definitely-missing-antigravity"
        with patch("worktogether.jobs.threading.Thread.start"):
            job = self.jobs.start(self.task)
        self.jobs._work(job, 30)
        self.assertEqual("FAILED", self.jobs.list(self.task)[0]["status"])
        self.assertEqual("BLOCKED", self.e.show(self.task)["status"])
