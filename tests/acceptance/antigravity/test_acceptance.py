"""Independent black-box acceptance test suite for worktogether core v1.

Owned by: Antigravity
Covers: ACC-001 through ACC-010 defined in qa/antigravity/acceptance.md
Constraints: Public API only (Engine and CLI), isolated temporary databases, no modification of production code.
"""
import json
import os
import subprocess
import sys
import tempfile
import unittest

from worktogether.core import CollabError, Engine


class AcceptanceTestBase(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.db_path = os.path.join(self.temp_dir.name, "test_collab.db")
        self.proj_dir = os.path.join(self.temp_dir.name, "project_repo")
        os.makedirs(self.proj_dir, exist_ok=True)
        self.engine = Engine(self.db_path)
        self.project = self.engine.project_add("TestProject", self.proj_dir)

    def tearDown(self):
        self.temp_dir.cleanup()

    def make_envelope(self, task_id, dispatch, status, summary, data,
                      basis=None, suggestions=None, next_action="Proceed"):
        return {
            "schema_version": 1,
            "task_id": task_id,
            "dispatch_id": dispatch["id"],
            "role": dispatch["role"],
            "attempt": dispatch["attempt"],
            "status": status,
            "summary": summary,
            "basis": basis or ["Observation or test artifact citation"],
            "suggestions": suggestions if suggestions is not None else [],
            "next_action": next_action,
            "data": data,
        }

    def make_plan_report(self, task_id, dispatch, steps):
        return self.make_envelope(
            task_id, dispatch, "DONE", "Initial plan created",
            {"steps": steps}, basis=["Requirement analysis"]
        )

    def make_review_report(self, task_id, dispatch, suggestions=None):
        return self.make_envelope(
            task_id, dispatch, "DONE", "Plan review completed",
            {}, basis=["Architecture review"],
            suggestions=suggestions or ["Consider adding boundary tests"]
        )

    def make_finalize_report(self, task_id, dispatch, steps):
        return self.make_envelope(
            task_id, dispatch, "DONE", "Plan finalized",
            {"steps": steps}, basis=["Incorporated review feedback"]
        )

    def make_code_report(self, task_id, dispatch, version, changes=None, known_limits=None):
        return self.make_envelope(
            task_id, dispatch, "DONE", f"Code implemented for {dispatch.get('step_id')}",
            {
                "version": version,
                "changes": changes or ["Implemented step logic"],
                "known_limits": known_limits or [],
            },
            basis=["Local compilation and syntax check"]
        )

    def make_verify_report(self, task_id, dispatch, version, checks, status="PASS", findings=None):
        return self.make_envelope(
            task_id, dispatch, status, f"Verification verdict: {status}",
            {
                "version": version,
                "checks": checks,
                "findings": findings or [],
            },
            basis=["Automated acceptance test suite execution"]
        )


class AcceptanceScenariosTest(AcceptanceTestBase):
    def test_acc_001_normal_lifecycle(self):
        """ACC-001: Baseline lifecycle from task creation to completion."""
        task = self.engine.task_add(self.project["id"], "Video Progress Feature", "Persist video playback progress")
        task_id = task["id"]
        self.assertEqual(task["status"], "ACTIVE")
        self.assertEqual(task["phase"], "PLANNING")

        # 1. PLAN
        d_plan = self.engine.dispatch(task_id)["dispatch"]
        self.assertIsNotNone(d_plan)
        self.assertEqual(d_plan["kind"], "PLAN")
        self.assertEqual(d_plan["role"], "codex")

        steps = [
            {
                "id": "s1",
                "title": "Local Storage Persistence",
                "acceptance": ["restore on reload", "clear at 100 percent"],
                "depends_on": [],
            }
        ]
        res = self.engine.submit(task_id, self.make_plan_report(task_id, d_plan, steps))
        self.assertTrue(res["accepted"])

        # 2. PLAN_REVIEW (Antigravity)
        t_show = self.engine.show(task_id)
        self.assertEqual(t_show["phase"], "PLAN_REVIEW")
        d_review = self.engine.dispatch(task_id)["dispatch"]
        self.assertIsNotNone(d_review)
        self.assertEqual(d_review["role"], "antigravity")
        self.assertEqual(d_review["kind"], "PLAN_REVIEW")

        res = self.engine.submit(task_id, self.make_review_report(task_id, d_review, ["Add timestamp clamping"]))
        self.assertTrue(res["accepted"])

        # 3. PLAN_FINALIZE (Codex)
        t_show = self.engine.show(task_id)
        self.assertEqual(t_show["phase"], "PLAN_FINALIZE")
        d_fin = self.engine.dispatch(task_id)["dispatch"]
        self.assertIsNotNone(d_fin)
        self.assertEqual(d_fin["role"], "codex")

        # Codex incorporates review
        final_steps = [
            {
                "id": "s1",
                "title": "Local Storage Persistence",
                "acceptance": ["restore on reload", "clear at 100 percent", "clamp invalid timestamps"],
                "depends_on": [],
            }
        ]
        res = self.engine.submit(task_id, self.make_finalize_report(task_id, d_fin, final_steps))
        self.assertTrue(res["accepted"])

        # 4. CODE (Codex)
        t_show = self.engine.show(task_id)
        self.assertEqual(t_show["phase"], "EXECUTING")
        d_code = self.engine.dispatch(task_id)["dispatch"]
        self.assertIsNotNone(d_code)
        self.assertEqual(d_code["kind"], "CODE")
        self.assertEqual(d_code["role"], "codex")
        self.assertEqual(d_code["step_id"], "s1")

        res = self.engine.submit(task_id, self.make_code_report(task_id, d_code, "commit_hash_v1", ["storage.ts"]))
        self.assertTrue(res["accepted"])

        # 5. VERIFY (Antigravity)
        d_verify = self.engine.dispatch(task_id)["dispatch"]
        self.assertIsNotNone(d_verify)
        self.assertEqual(d_verify["kind"], "VERIFY")
        self.assertEqual(d_verify["role"], "antigravity")
        self.assertEqual(d_verify["version"], "commit_hash_v1")

        checks = [
            {"criterion": "restore on reload", "status": "PASS", "evidence": ["test_reload passed"]},
            {"criterion": "clear at 100 percent", "status": "PASS", "evidence": ["test_cleanup passed"]},
            {"criterion": "clamp invalid timestamps", "status": "PASS", "evidence": ["test_clamping passed"]},
        ]
        res = self.engine.submit(task_id, self.make_verify_report(task_id, d_verify, "commit_hash_v1", checks))
        self.assertTrue(res["accepted"])

        # Final check: Task and steps are COMPLETED
        t_final = self.engine.show(task_id)
        self.assertEqual(t_final["status"], "COMPLETED")
        self.assertEqual(t_final["phase"], "COMPLETED")
        self.assertEqual(t_final["steps"][0]["status"], "COMPLETED")

    def test_acc_002_verification_fail_and_fix(self):
        """ACC-002: Verification fails, triggers FIX, then passes on re-verification."""
        task = self.engine.task_add(self.project["id"], "Progress Feature", "Requirement")
        task_id = task["id"]

        # Fast-forward to CODE
        d_plan = self.engine.dispatch(task_id)["dispatch"]
        steps = [{"id": "s1", "title": "Step 1", "acceptance": ["crit1"], "depends_on": []}]
        self.engine.submit(task_id, self.make_plan_report(task_id, d_plan, steps))
        d_review = self.engine.dispatch(task_id)["dispatch"]
        self.engine.submit(task_id, self.make_review_report(task_id, d_review, []))
        d_fin = self.engine.dispatch(task_id)["dispatch"]
        self.engine.submit(task_id, self.make_finalize_report(task_id, d_fin, steps))

        # CODE v1
        d_code = self.engine.dispatch(task_id)["dispatch"]
        self.engine.submit(task_id, self.make_code_report(task_id, d_code, "v1"))

        # VERIFY v1 -> FAIL
        d_verify1 = self.engine.dispatch(task_id)["dispatch"]
        checks_fail = [{"criterion": "crit1", "status": "FAIL", "evidence": ["test failed exit_code=1"]}]
        findings = [
            {
                "problem": "State not restored",
                "reproduction": ["Reload page"],
                "expected": "Progress restored",
                "actual": "Progress 0",
                "evidence": ["fail_log_01.txt"],
            }
        ]
        res = self.engine.submit(task_id, self.make_verify_report(task_id, d_verify1, "v1", checks_fail, "FAIL", findings))
        self.assertTrue(res["accepted"])

        # Verify step entered FIXING with repair_count=1
        t_show = self.engine.show(task_id)
        self.assertEqual(t_show["steps"][0]["status"], "FIXING")
        self.assertEqual(t_show["steps"][0]["repair_count"], 1)

        # FIX dispatch to Codex
        d_fix = self.engine.dispatch(task_id)["dispatch"]
        self.assertIsNotNone(d_fix)
        self.assertEqual(d_fix["kind"], "FIX")
        self.assertEqual(d_fix["role"], "codex")
        self.assertEqual(d_fix["attempt"], 1)  # Newly issued FIX dispatch starts at attempt 1

        # Codex submits new version v2 (must not be v1)
        self.engine.submit(task_id, self.make_code_report(task_id, d_fix, "v2", ["Fixed state restore"]))

        # VERIFY v2 -> PASS
        d_verify2 = self.engine.dispatch(task_id)["dispatch"]
        self.assertEqual(d_verify2["version"], "v2")
        checks_pass = [{"criterion": "crit1", "status": "PASS", "evidence": ["test passed exit_code=0"]}]
        res = self.engine.submit(task_id, self.make_verify_report(task_id, d_verify2, "v2", checks_pass, "PASS"))
        self.assertTrue(res["accepted"])

        t_final = self.engine.show(task_id)
        self.assertEqual(t_final["status"], "COMPLETED")
        self.assertEqual(t_final["steps"][0]["status"], "COMPLETED")
        self.assertEqual(t_final["steps"][0]["repair_count"], 1)

    def test_acc_003_repair_limit_blocks(self):
        """ACC-003: Max 2 repairs exceeded causes BLOCKED / REPAIR_LIMIT."""
        task = self.engine.task_add(self.project["id"], "Task", "Req")
        task_id = task["id"]

        # Fast forward to CODE
        d_plan = self.engine.dispatch(task_id)["dispatch"]
        steps = [{"id": "s1", "title": "Step 1", "acceptance": ["crit1"], "depends_on": []}]
        self.engine.submit(task_id, self.make_plan_report(task_id, d_plan, steps))
        d_review = self.engine.dispatch(task_id)["dispatch"]
        self.engine.submit(task_id, self.make_review_report(task_id, d_review, []))
        d_fin = self.engine.dispatch(task_id)["dispatch"]
        self.engine.submit(task_id, self.make_finalize_report(task_id, d_fin, steps))

        # Initial CODE (v1)
        d_code = self.engine.dispatch(task_id)["dispatch"]
        self.engine.submit(task_id, self.make_code_report(task_id, d_code, "v1"))

        def fail_verification(ver):
            d = self.engine.dispatch(task_id)["dispatch"]
            self.assertEqual(d["kind"], "VERIFY")
            checks = [{"criterion": "crit1", "status": "FAIL", "evidence": ["failed"]}]
            findings = [{"problem": "p", "reproduction": ["r"], "expected": "e", "actual": "a", "evidence": ["ev"]}]
            self.engine.submit(task_id, self.make_verify_report(task_id, d, ver, checks, "FAIL", findings))

        # Fail 1 -> triggers FIX 1 (repair_count becomes 1)
        fail_verification("v1")
        d_fix1 = self.engine.dispatch(task_id)["dispatch"]
        self.assertEqual(d_fix1["kind"], "FIX")
        self.engine.submit(task_id, self.make_code_report(task_id, d_fix1, "v2"))

        # Fail 2 -> triggers FIX 2 (repair_count becomes 2)
        fail_verification("v2")
        d_fix2 = self.engine.dispatch(task_id)["dispatch"]
        self.assertEqual(d_fix2["kind"], "FIX")
        self.engine.submit(task_id, self.make_code_report(task_id, d_fix2, "v3"))

        # Fail 3 -> exceeds max repairs (2). Step and task become BLOCKED with REPAIR_LIMIT.
        fail_verification("v3")

        t_blocked = self.engine.show(task_id)
        self.assertEqual(t_blocked["status"], "BLOCKED")
        self.assertEqual(t_blocked["block_reason"], "REPAIR_LIMIT")
        self.assertEqual(t_blocked["steps"][0]["status"], "BLOCKED")
        self.assertTrue(t_blocked["steps"][0]["repair_exhausted"])

        # Subsequent dispatch returns None
        self.assertIsNone(self.engine.dispatch(task_id)["dispatch"])

        # Explicit retry is strictly disallowed for REPAIR_LIMIT
        last_dispatch_id = t_blocked["dispatches"][-1]["id"]
        with self.assertRaises(CollabError) as ctx:
            self.engine.retry(task_id, last_dispatch_id)
        self.assertEqual(ctx.exception.code, "INVALID_STATE")

    def test_acc_004_duplicate_and_conflict(self):
        """ACC-004: Duplicate identical report is idempotent; conflicting report is rejected."""
        task = self.engine.task_add(self.project["id"], "Task", "Req")
        task_id = task["id"]
        d = self.engine.dispatch(task_id)["dispatch"]

        steps = [{"id": "s1", "title": "Step 1", "acceptance": ["crit1"], "depends_on": []}]
        report1 = self.make_plan_report(task_id, d, steps)

        # First submission succeeds
        res1 = self.engine.submit(task_id, report1)
        self.assertTrue(res1["accepted"])
        self.assertFalse(res1.get("duplicate", False))

        events_before = len(self.engine.show(task_id)["events"])

        # Identical re-submission returns accepted=True, duplicate=True
        res2 = self.engine.submit(task_id, report1)
        self.assertTrue(res2["accepted"])
        self.assertTrue(res2["duplicate"])

        # Assert no duplicate events were created
        events_after = len(self.engine.show(task_id)["events"])
        self.assertEqual(events_before, events_after)

        # Different submission for the same dispatch returns CONFLICT
        report_conflict = self.make_plan_report(task_id, d, steps)
        report_conflict["summary"] = "Different conflicting summary"
        with self.assertRaises(CollabError) as ctx:
            self.engine.submit(task_id, report_conflict)
        self.assertEqual(ctx.exception.code, "CONFLICT")

    def test_acc_005_stale_dispatch_and_version_mismatch(self):
        """ACC-005: Stale dispatch / version mismatch is rejected with STALE_REPORT."""
        task = self.engine.task_add(self.project["id"], "Task", "Req")
        task_id = task["id"]

        # Fast forward to CODE
        d_plan = self.engine.dispatch(task_id)["dispatch"]
        steps = [{"id": "s1", "title": "Step 1", "acceptance": ["crit1"], "depends_on": []}]
        self.engine.submit(task_id, self.make_plan_report(task_id, d_plan, steps))
        d_review = self.engine.dispatch(task_id)["dispatch"]
        self.engine.submit(task_id, self.make_review_report(task_id, d_review, []))
        d_fin = self.engine.dispatch(task_id)["dispatch"]
        self.engine.submit(task_id, self.make_finalize_report(task_id, d_fin, steps))

        d_code = self.engine.dispatch(task_id)["dispatch"]
        self.engine.submit(task_id, self.make_code_report(task_id, d_code, "v1"))

        # VERIFY dispatch targeting v1
        d_verify = self.engine.dispatch(task_id)["dispatch"]

        # 1. Version mismatch test
        mismatched_report = self.make_verify_report(
            task_id, d_verify, "stale_outdated_commit_sha",
            [{"criterion": "crit1", "status": "PASS", "evidence": ["ev"]}]
        )
        with self.assertRaises(CollabError) as ctx:
            self.engine.submit(task_id, mismatched_report)
        self.assertEqual(ctx.exception.code, "STALE_REPORT")

        # 2. Attempt mismatch test
        wrong_attempt_report = self.make_verify_report(
            task_id, d_verify, "v1",
            [{"criterion": "crit1", "status": "PASS", "evidence": ["ev"]}]
        )
        wrong_attempt_report["attempt"] = 999
        with self.assertRaises(CollabError) as ctx:
            self.engine.submit(task_id, wrong_attempt_report)
        self.assertEqual(ctx.exception.code, "STALE_REPORT")

    def test_acc_006_project_and_task_isolation(self):
        """ACC-006: Tasks in different projects are isolated; cross-task submissions are rejected."""
        proj2_dir = os.path.join(self.temp_dir.name, "proj2_repo")
        os.makedirs(proj2_dir, exist_ok=True)
        proj2 = self.engine.project_add("ProjectBeta", proj2_dir)

        task_a = self.engine.task_add(self.project["id"], "Task A", "Req A")
        task_b = self.engine.task_add(proj2["id"], "Task B", "Req B")

        d_a = self.engine.dispatch(task_a["id"])["dispatch"]
        d_b = self.engine.dispatch(task_b["id"])["dispatch"]

        self.assertIsNotNone(d_a)
        self.assertIsNotNone(d_b)

        # Submit task A's report to task B -> rejected
        steps = [{"id": "s1", "title": "S1", "acceptance": ["c1"], "depends_on": []}]
        report_a = self.make_plan_report(task_a["id"], d_a, steps)
        with self.assertRaises(CollabError) as ctx:
            self.engine.submit(task_b["id"], report_a)
        self.assertIn(ctx.exception.code, ("NOT_FOUND", "INVALID_REPORT"))

        # Advance Task B independently
        report_b = self.make_plan_report(task_b["id"], d_b, steps)
        res_b = self.engine.submit(task_b["id"], report_b)
        self.assertTrue(res_b["accepted"])

        # Task A remains in PLANNING phase
        self.assertEqual(self.engine.show(task_a["id"])["phase"], "PLANNING")
        self.assertEqual(self.engine.show(task_b["id"])["phase"], "PLAN_REVIEW")

    def test_acc_007_pause_accepts_inflight_and_resumes(self):
        """ACC-007: Pause stops new dispatches, accepts in-flight results, and resumes cleanly."""
        task = self.engine.task_add(self.project["id"], "Task", "Req")
        task_id = task["id"]
        d = self.engine.dispatch(task_id)["dispatch"]
        self.assertIsNotNone(d)

        # Pause task while dispatch is active
        res_pause = self.engine.pause(task_id)
        self.assertEqual(res_pause["status"], "PAUSED")
        self.assertEqual(self.engine.show(task_id)["status"], "PAUSED")

        # New dispatch is blocked while paused
        self.assertIsNone(self.engine.dispatch(task_id)["dispatch"])

        # Submit in-flight report
        steps = [{"id": "s1", "title": "S1", "acceptance": ["c1"], "depends_on": []}]
        report = self.make_plan_report(task_id, d, steps)
        res_submit = self.engine.submit(task_id, report)
        self.assertTrue(res_submit["accepted"])

        # Task phase updated, but status remains PAUSED
        t_show = self.engine.show(task_id)
        self.assertEqual(t_show["phase"], "PLAN_REVIEW")
        self.assertEqual(t_show["status"], "PAUSED")

        # Still no new dispatch while paused
        self.assertIsNone(self.engine.dispatch(task_id)["dispatch"])

        # Resume task
        res_resume = self.engine.resume(task_id)
        self.assertEqual(res_resume["status"], "ACTIVE")

        # Now dispatch succeeds for PLAN_REVIEW
        d_next = self.engine.dispatch(task_id)["dispatch"]
        self.assertIsNotNone(d_next)
        self.assertEqual(d_next["kind"], "PLAN_REVIEW")

    def test_acc_008_process_restart_crash_recovery(self):
        """ACC-008: State and active dispatches survive process restart without duplicate dispatch."""
        task = self.engine.task_add(self.project["id"], "Task", "Req")
        task_id = task["id"]
        d1 = self.engine.dispatch(task_id)["dispatch"]
        self.assertIsNotNone(d1)

        # Simulate process termination by creating a fresh Engine on the same SQLite file
        new_engine = Engine(self.db_path)
        t_recovered = new_engine.show(task_id)
        self.assertEqual(t_recovered["status"], "ACTIVE")
        self.assertEqual(t_recovered["phase"], "PLANNING")
        self.assertEqual(len(t_recovered["dispatches"]), 1)
        self.assertEqual(t_recovered["dispatches"][0]["status"], "ACTIVE")

        # Fresh instance does NOT re-issue already active dispatch
        self.assertIsNone(new_engine.dispatch(task_id)["dispatch"])

        # Fresh instance accepts report for d1
        steps = [{"id": "s1", "title": "S1", "acceptance": ["c1"], "depends_on": []}]
        res = new_engine.submit(task_id, self.make_plan_report(task_id, d1, steps))
        self.assertTrue(res["accepted"])
        self.assertEqual(new_engine.show(task_id)["phase"], "PLAN_REVIEW")

    def test_acc_009_invalid_input_and_state_invariance(self):
        """ACC-009: Malformed/invalid reports are rejected and transactionally rolled back."""
        task = self.engine.task_add(self.project["id"], "Task", "Req")
        task_id = task["id"]

        # Setup to VERIFY phase
        d_plan = self.engine.dispatch(task_id)["dispatch"]
        steps = [{"id": "s1", "title": "S1", "acceptance": ["critA", "critB"], "depends_on": []}]
        self.engine.submit(task_id, self.make_plan_report(task_id, d_plan, steps))
        d_review = self.engine.dispatch(task_id)["dispatch"]
        self.engine.submit(task_id, self.make_review_report(task_id, d_review, []))
        d_fin = self.engine.dispatch(task_id)["dispatch"]
        self.engine.submit(task_id, self.make_finalize_report(task_id, d_fin, steps))
        d_code = self.engine.dispatch(task_id)["dispatch"]
        self.engine.submit(task_id, self.make_code_report(task_id, d_code, "v1"))

        d_verify = self.engine.dispatch(task_id)["dispatch"]
        state_before = self.engine.show(task_id)

        # 9a: Missing evidence in check
        rep_missing_ev = self.make_verify_report(
            task_id, d_verify, "v1",
            [
                {"criterion": "critA", "status": "PASS", "evidence": []},  # Empty evidence
                {"criterion": "critB", "status": "PASS", "evidence": ["evB"]},
            ]
        )
        with self.assertRaises(CollabError) as ctx:
            self.engine.submit(task_id, rep_missing_ev)
        self.assertEqual(ctx.exception.code, "INVALID_REPORT")
        self.assertEqual(self.engine.show(task_id), state_before)

        # 9b: Incomplete criteria checked
        rep_incomplete = self.make_verify_report(
            task_id, d_verify, "v1",
            [{"criterion": "critA", "status": "PASS", "evidence": ["evA"]}]  # Missing critB
        )
        with self.assertRaises(CollabError) as ctx:
            self.engine.submit(task_id, rep_incomplete)
        self.assertEqual(ctx.exception.code, "INVALID_REPORT")
        self.assertEqual(self.engine.show(task_id), state_before)

        # 9c: Extra unknown fields
        rep_unknown_field = self.make_verify_report(
            task_id, d_verify, "v1",
            [
                {"criterion": "critA", "status": "PASS", "evidence": ["evA"]},
                {"criterion": "critB", "status": "PASS", "evidence": ["evB"]},
            ]
        )
        rep_unknown_field["unexpected_field"] = "malicious_payload"
        with self.assertRaises(CollabError) as ctx:
            self.engine.submit(task_id, rep_unknown_field)
        self.assertEqual(ctx.exception.code, "INVALID_REPORT")
        self.assertEqual(self.engine.show(task_id), state_before)

        # 9d: BLOCKED report transitions dispatch and step to BLOCKED (not PASS)
        rep_blocked = self.make_envelope(
            task_id, d_verify, "BLOCKED", "Environment down",
            {"reason": "Mock server unavailable"}
        )
        res_blocked = self.engine.submit(task_id, rep_blocked)
        self.assertTrue(res_blocked["accepted"])
        t_blocked = self.engine.show(task_id)
        self.assertEqual(t_blocked["status"], "BLOCKED")
        self.assertEqual(t_blocked["block_reason"], "Mock server unavailable")
        self.assertEqual(t_blocked["steps"][0]["status"], "BLOCKED")

    def test_acc_010_dependencies_and_checkout_safety(self):
        """ACC-010: Dependent steps are gated; one checkout cannot mix CODE and VERIFY."""
        task = self.engine.task_add(self.project["id"], "Task", "Req")
        task_id = task["id"]

        d_plan = self.engine.dispatch(task_id)["dispatch"]
        # s1 and s2 independent, s3 depends on s1
        steps = [
            {"id": "s1", "title": "Step 1", "acceptance": ["c1"], "depends_on": []},
            {"id": "s2", "title": "Step 2", "acceptance": ["c2"], "depends_on": []},
            {"id": "s3", "title": "Step 3", "acceptance": ["c3"], "depends_on": ["s1"]},
        ]
        self.engine.submit(task_id, self.make_plan_report(task_id, d_plan, steps))
        d_review = self.engine.dispatch(task_id)["dispatch"]
        self.engine.submit(task_id, self.make_review_report(task_id, d_review, []))
        d_fin = self.engine.dispatch(task_id)["dispatch"]
        self.engine.submit(task_id, self.make_finalize_report(task_id, d_fin, steps))

        # First CODE dispatch for s1
        d_code_s1 = self.engine.dispatch(task_id)["dispatch"]
        self.assertEqual(d_code_s1["step_id"], "s1")
        self.assertEqual(d_code_s1["role"], "codex")

        # While Codex is CODING s1, another CODE cannot be dispatched (Codex is busy)
        self.assertIsNone(self.engine.dispatch(task_id)["dispatch"])

        # Codex completes s1 -> enters VERIFYING
        self.engine.submit(task_id, self.make_code_report(task_id, d_code_s1, "v1"))

        d_verify = self.engine.dispatch(task_id)["dispatch"]
        self.assertEqual(d_verify["kind"], "VERIFY")
        self.assertEqual(d_verify["role"], "antigravity")
        self.assertEqual(self.engine.dispatch(task_id)["reason"], "PROJECT_WORKSPACE_BUSY")

        checks = [{"criterion": "c1", "status": "PASS", "evidence": ["fixture verification"]}]
        self.engine.submit(task_id, self.make_verify_report(task_id, d_verify, "v1", checks))
        d_code_s2 = self.engine.dispatch(task_id)["dispatch"]
        self.assertEqual(d_code_s2["kind"], "CODE")
        self.assertEqual(d_code_s2["step_id"], "s2")
        # s3 depends on s1, but it still cannot write alongside s2 in one checkout.
        self.assertEqual(self.engine.dispatch(task_id)["reason"], "PROJECT_WORKSPACE_BUSY")

    def test_acc_cli_invocations(self):
        """Verify public CLI commands via python3 -m worktogether."""
        # 1. doctor
        cmd = [sys.executable, "-m", "worktogether", "--db", self.db_path, "doctor", "--project", self.project["id"]]
        out = subprocess.check_output(cmd)
        res = json.loads(out)
        self.assertEqual(res["project"]["id"], self.project["id"])
        self.assertIn("git", res)
        self.assertIn("executables", res)

        # 2. task-add via CLI
        cmd = [
            sys.executable, "-m", "worktogether", "--db", self.db_path,
            "task-add", "--project", self.project["id"],
            "--title", "CLI Task", "--requirement", "Test via CLI"
        ]
        out = subprocess.check_output(cmd)
        res = json.loads(out)
        cli_task_id = res["id"]
        self.assertEqual(res["title"], "CLI Task")

        # 3. show via CLI
        cmd = [sys.executable, "-m", "worktogether", "--db", self.db_path, "show", "--task", cli_task_id]
        out = subprocess.check_output(cmd)
        res = json.loads(out)
        self.assertEqual(res["id"], cli_task_id)
        self.assertEqual(res["status"], "ACTIVE")

        # 4. pause and resume via CLI
        cmd = [sys.executable, "-m", "worktogether", "--db", self.db_path, "pause", "--task", cli_task_id]
        out = subprocess.check_output(cmd)
        res = json.loads(out)
        self.assertEqual(res["status"], "PAUSED")

        cmd = [sys.executable, "-m", "worktogether", "--db", self.db_path, "resume", "--task", cli_task_id]
        out = subprocess.check_output(cmd)
        res = json.loads(out)
        self.assertEqual(res["status"], "ACTIVE")


if __name__ == "__main__":
    unittest.main()
