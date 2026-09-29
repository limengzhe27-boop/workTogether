import concurrent.futures
import copy
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from worktogether.adapters import MockAdapter
from worktogether.core import CollabError, Engine


class EngineTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.path = str(Path(self.temp.name) / "state.sqlite3")
        self.e = Engine(self.path)
        self.p = self.e.project_add("test", self.temp.name)["id"]
        self.t = self.e.task_add(self.p, "task", "Build behavior")["id"]

    def issue(self):
        d = self.e.dispatch(self.t)["dispatch"]
        self.assertIsNotNone(d)
        return d

    def report(self, d):
        return MockAdapter().prepare(d, self.e.template(self.t, d["id"]))

    def complete(self):
        d = self.issue()
        self.e.submit(self.t, self.report(d))
        return d

    def plan(self, steps=None):
        for _ in range(3):
            d = self.issue()
            report = self.report(d)
            if steps and d["kind"] in ("PLAN", "PLAN_FINALIZE"):
                report["data"] = {"steps": steps}
            self.e.submit(self.t, report)

    def fail(self, d):
        report = self.report(d)
        report["status"] = "FAIL"
        report["data"]["checks"][0]["status"] = "FAIL"
        report["data"]["findings"] = [{"problem": "Lost data", "reproduction": ["Refresh"],
                                          "expected": "Persist", "actual": "Lost", "evidence": ["log: reproduced"]}]
        return report

    def assert_error(self, code, fn):
        with self.assertRaises(CollabError) as caught:
            fn()
        self.assertEqual(code, caught.exception.code)

    def test_full_cycle_and_restart(self):
        self.plan()
        self.complete()
        d = self.issue()
        self.e = Engine(self.path)
        self.assertIsNone(self.e.dispatch(self.t)["dispatch"])
        self.e.submit(self.t, self.report(d))
        self.assertEqual("COMPLETED", self.e.show(self.t)["status"])

    def test_repair_limit(self):
        self.plan()
        self.complete()
        for repair in range(3):
            d = self.issue()
            self.assertEqual("VERIFY", d["kind"])
            self.e.submit(self.t, self.fail(d))
            if repair < 2:
                self.assertEqual("FIX", self.complete()["kind"])
        state = self.e.show(self.t)
        self.assertEqual("BLOCKED", state["status"])
        self.assertEqual("REPAIR_LIMIT", state["block_reason"])
        self.assertEqual(2, state["steps"][0]["repair_count"])
        self.assertIsNone(self.e.dispatch(self.t)["dispatch"])
        self.assert_error("INVALID_STATE", lambda: self.e.retry(self.t, d["id"]))

    def test_identical_duplicate_and_conflict(self):
        d = self.issue()
        report = self.report(d)
        self.e.submit(self.t, report)
        count = len(self.e.show(self.t)["events"])
        self.assertTrue(self.e.submit(self.t, report)["duplicate"])
        self.assertEqual(count, len(self.e.show(self.t)["events"]))
        report["summary"] = "Different"
        self.assert_error("CONFLICT", lambda: self.e.submit(self.t, report))

    def test_cross_task_role_and_attempt_rejected(self):
        d = self.issue()
        report = self.report(d)
        other = self.e.task_add(self.p, "other", "Other requirement")["id"]
        self.assert_error("NOT_FOUND", lambda: self.e.submit(other, report))
        for key, value, code in [("role", "antigravity", "INVALID_REPORT"),
                                  ("task_id", other, "INVALID_REPORT"),
                                  ("attempt", 2, "STALE_REPORT")]:
            bad = dict(report, **{key: value})
            self.assert_error(code, lambda: self.e.submit(self.t, bad))
        self.assertEqual("ACTIVE", self.e.show(self.t)["dispatches"][0]["status"])

    def test_pause_accepts_result_no_dispatch_until_resume(self):
        d = self.issue()
        self.e.pause(self.t)
        self.e.submit(self.t, self.report(d))
        self.assertEqual("PAUSED", self.e.show(self.t)["status"])
        self.assertIsNone(self.e.dispatch(self.t)["dispatch"])
        self.e.resume(self.t)
        self.assertEqual("PLAN_REVIEW", self.issue()["kind"])

    def test_pause_at_final_completion(self):
        self.plan()
        self.complete()
        d = self.issue()
        self.e.pause(self.t)
        self.e.submit(self.t, self.report(d))
        self.assertEqual("PAUSED", self.e.show(self.t)["status"])
        self.assertEqual("COMPLETED", self.e.resume(self.t)["status"])

    def test_timeout_requires_explicit_retry(self):
        d = self.issue()
        report = self.report(d)
        with patch("worktogether.core.time.time", return_value=d["deadline"] + 1):
            self.assert_error("STALE_REPORT", lambda: self.e.submit(self.t, report))
            self.assertEqual([d["id"]], self.e.expire(self.t)["expired"])
        new = self.e.retry(self.t, d["id"])["dispatch"]
        self.assertEqual(2, new["attempt"])
        self.assertNotEqual(d["id"], new["id"])
        self.assert_error("STALE_REPORT", lambda: self.e.submit(self.t, report))
        self.e.submit(self.t, self.report(new))

    def test_blocked_report_retry(self):
        d = self.issue()
        r = self.report(d)
        r.update(status="BLOCKED", data={"reason": "Missing environment"})
        self.e.submit(self.t, r)
        self.assertEqual("BLOCKED", self.e.show(self.t)["status"])
        new = self.e.retry(self.t, d["id"])["dispatch"]
        self.e.submit(self.t, self.report(new))
        self.assertEqual("PLAN_REVIEW", self.e.show(self.t)["phase"])

    def test_version_and_evidence_validation(self):
        self.plan()
        self.complete()
        d = self.issue()
        report = self.report(d)
        bad = copy.deepcopy(report)
        bad["data"]["version"] = "old"
        self.assert_error("STALE_REPORT", lambda: self.e.submit(self.t, bad))
        for checks in ([], [{"criterion": "Simulated behavior", "status": "PASS", "evidence": []}]):
            bad = copy.deepcopy(report)
            bad["data"]["checks"] = checks
            self.assert_error("INVALID_REPORT", lambda: self.e.submit(self.t, bad))
        self.e.submit(self.t, report)

    def test_invalid_plan_atomic(self):
        d = self.issue()
        report = self.report(d)
        report["data"]["steps"][0]["depends_on"] = ["s1"]
        self.assert_error("INVALID_PLAN", lambda: self.e.submit(self.t, report))
        self.assertEqual("PLANNING", self.e.show(self.t)["phase"])

    def test_review_only_once_max_three_suggestions(self):
        self.complete()
        d = self.issue()
        report = self.report(d)
        report["suggestions"] = ["a", "b", "c", "d"]
        self.assert_error("INVALID_REPORT", lambda: self.e.submit(self.t, report))
        report["suggestions"] = ["a"]
        self.e.submit(self.t, report)
        self.assertEqual("PLAN_FINALIZE", self.complete()["kind"])
        self.assertEqual("CODE", self.issue()["kind"])

    def test_overlap_and_dependencies(self):
        steps = [{"id": k, "title": k, "acceptance": [k], "depends_on": deps}
                 for k, deps in [("a", []), ("b", []), ("c", ["a"])]]
        self.plan(steps)
        self.assertEqual("a", self.complete()["step_id"])
        verify = self.issue()
        self.assertEqual("VERIFY", verify["kind"])
        code = self.issue()
        self.assertEqual("b", code["step_id"])
        self.assertIsNone(self.e.dispatch(self.t)["dispatch"])
        self.e.submit(self.t, self.report(code))
        self.assertIsNone(self.e.dispatch(self.t)["dispatch"])
        self.e.submit(self.t, self.report(verify))
        self.assertEqual("b", self.issue()["step_id"])
        self.assertEqual("c", self.issue()["step_id"])

    def test_concurrent_dispatch_once(self):
        def call(_):
            return Engine(self.path).dispatch(self.t)["dispatch"]
        with concurrent.futures.ThreadPoolExecutor(max_workers=8) as pool:
            results = list(pool.map(call, range(16)))
        self.assertEqual(1, sum(d is not None for d in results))

    def test_project_writer_lock_and_separate_projects(self):
        self.issue()
        other = self.e.task_add(self.p, "other", "Other")["id"]
        self.assertIsNone(self.e.dispatch(other)["dispatch"])
        directory = Path(self.temp.name) / "second"
        directory.mkdir()
        p2 = self.e.project_add("second", str(directory))["id"]
        t2 = self.e.task_add(p2, "other", "Other")["id"]
        self.assertIsNotNone(self.e.dispatch(t2)["dispatch"])

    def test_cli_demo_and_error_json(self):
        base = [sys.executable, "-m", "worktogether", "--db", str(Path(self.temp.name) / "cli.sqlite3")]
        r = subprocess.run(base + ["demo"], text=True, capture_output=True)
        self.assertEqual(0, r.returncode, r.stderr)
        result = json.loads(r.stdout)
        self.assertTrue(result["simulated"])
        self.assertEqual("COMPLETED", result["task"]["status"])
        r = subprocess.run(base + ["show", "--task", "missing"], text=True, capture_output=True)
        self.assertEqual(2, r.returncode)
        self.assertEqual("NOT_FOUND", json.loads(r.stdout)["error"]["code"])

    def test_repair_limit_survives_other_dispatch_timeout(self):
        self.plan([{"id": k, "title": k, "acceptance": [k], "depends_on": []} for k in ("a", "b")])
        self.complete()
        for _ in range(2):
            d = self.issue()
            self.e.submit(self.t, self.fail(d))
            self.assertEqual("FIX", self.complete()["kind"])
        verify = self.issue()
        other_code = self.issue()
        self.assertEqual("b", other_code["step_id"])
        self.e.submit(self.t, self.fail(verify))
        with patch("worktogether.core.time.time", return_value=other_code["deadline"] + 1):
            self.e.expire(self.t)
        self.assertEqual("REPAIR_LIMIT", self.e.show(self.t)["block_reason"])
        self.assert_error("INVALID_STATE", lambda: self.e.retry(self.t, other_code["id"]))

    def test_malformed_reports_rollback(self):
        d = self.issue()
        original = self.report(d)
        malformed = [None, [], {}, dict(original, schema_version=True), dict(original, data=[]),
                     dict(original, extra="not allowed"), dict(original, basis=[])]
        before = self.e.show(self.t)
        for report in malformed:
            with self.assertRaises(CollabError):
                self.e.submit(self.t, report)
            self.assertEqual(before, self.e.show(self.t))

    def test_verification_template_does_not_claim_pass(self):
        self.plan()
        self.complete()
        d = self.issue()
        template = self.e.template(self.t, d["id"])
        self.assertEqual("BLOCKED", template["status"])


if __name__ == "__main__":
    unittest.main()
