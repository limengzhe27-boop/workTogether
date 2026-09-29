import tempfile
import unittest
from pathlib import Path

from worktogether.adapters import MockAdapter
from worktogether.core import Engine, CollabError
from worktogether.execution import execute
from worktogether.runtime import Runtime


class ExecutionTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.e = Engine(Path(self.tmp.name) / "state.sqlite3")
        p = self.e.project_add("fixture", self.tmp.name)
        self.task = self.e.task_add(p["id"], "fixture", "simulated only")["id"]
        self.c = Runtime(self.e)
        self.calls = []
        self.failure = False
        self.reject = False
        self.pause = False
        for _ in range(3):
            d = self.e.dispatch(self.task)["dispatch"]
            self.e.submit(self.task, MockAdapter().prepare(d, self.e.template(self.task, d["id"])))
        self.c.approval(self.task, self.c.approval(self.task)["digest"])
        self.c.run = self.run_fixture

    def run_fixture(self, task, dispatch_id, timeout):
        d = next(d for d in self.e.show(task)["dispatches"] if d["id"] == dispatch_id)
        self.calls.append(d["kind"])
        if self.failure:
            return {"status": "FAILED"}
        report = MockAdapter().prepare(d, self.e.template(task, dispatch_id))
        if self.reject and d["kind"] == "VERIFY":
            report["status"] = "FAIL"
            report["data"]["checks"][0]["status"] = "FAIL"
            report["data"]["findings"] = [{"problem": "simulated", "reproduction": ["simulated"],
                "expected": "pass", "actual": "fail", "evidence": ["fixture only"]}]
        self.e.submit(task, report)
        if self.pause:
            self.e.pause(task)
        return {"status": "COMPLETED"}

    def go(self, **kwargs):
        return execute(self.e, self.task, self.c, self.c, **kwargs)

    def test_complete(self):
        self.assertEqual("COMPLETED", self.go()["reason"])
        self.assertEqual(["CODE", "VERIFY"], self.calls)

    def test_second_controller_is_rejected(self):
        original = self.c.run
        nested = []
        def run(task, dispatch_id, timeout):
            nested.append(self.go()["reason"])
            return original(task, dispatch_id, timeout)
        self.c.run = run
        self.assertEqual("COMPLETED", self.go()["reason"])
        self.assertEqual(["EXECUTION_BUSY", "EXECUTION_BUSY"], nested)

    def test_call_budget_and_explicit_continuation(self):
        self.assertEqual("CALL_LIMIT", self.go(max_calls=1)["reason"])
        self.assertEqual("COMPLETED", self.go(max_calls=1)["reason"])

    def test_missing_approval_does_not_dispatch(self):
        with self.e.transaction() as db:
            db.execute("DELETE FROM approvals")
        self.assertEqual("APPROVAL_REQUIRED", self.go()["reason"])
        self.assertEqual([], self.calls)

    def test_pause_stops_before_verification(self):
        self.pause = True
        self.assertEqual("TASK_NOT_ACTIVE", self.go()["reason"])
        self.assertEqual(["CODE"], self.calls)

    def test_provider_failure_not_retried(self):
        self.failure = True
        self.assertEqual("PROVIDER_FAILED", self.go()["reason"])
        self.assertEqual("EXISTING_DISPATCH", self.go()["reason"])
        self.assertEqual(["CODE"], self.calls)

    def test_repair_limit(self):
        self.reject = True
        self.go(max_calls=10)
        self.assertEqual(["CODE", "VERIFY", "FIX", "VERIFY", "FIX", "VERIFY"], self.calls)
        self.assertEqual("REPAIR_LIMIT", self.e.show(self.task)["block_reason"])

    def test_queued_message_stops(self):
        self.e.message_add(self.task, "message-one", "codex", "wait please")
        self.assertEqual("MESSAGE_PENDING", self.go()["reason"])
        self.assertEqual([], self.calls)

    def test_invalid_budget(self):
        for value in (0, 31, True, 1.5):
            with self.assertRaises(CollabError):
                self.go(max_calls=value)

    def test_web_job_persists_and_duplicate_start_rejected(self):
        from unittest.mock import patch
        from worktogether.jobs import ExecutionJobs
        jobs = ExecutionJobs(self.e, self.c, self.c)
        with patch('worktogether.jobs.threading.Thread.start'):
            job = jobs.start(self.task)
            with self.assertRaises(CollabError):
                jobs.start(self.task)
        self.assertEqual('QUEUED', ExecutionJobs(self.e, self.c, self.c).list(self.task)[0]['status'])
        jobs._work(job, 300)
        record = jobs.list(self.task)[0]
        self.assertEqual('COMPLETED', record['result']['reason'])
        self.assertEqual(['CODE', 'VERIFY'], self.calls)

    def test_web_job_rejects_unapproved_plan(self):
        from worktogether.jobs import ExecutionJobs
        with self.e.transaction() as db:
            db.execute('DELETE FROM approvals')
        jobs = ExecutionJobs(self.e, self.c, self.c)
        with self.assertRaises(CollabError):
            jobs.start(self.task)
        self.assertEqual([], jobs.list(self.task))
