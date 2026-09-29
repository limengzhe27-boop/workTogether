"""A complete, deterministic discussion -> assignment -> analysis -> review path."""
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from worktogether.agents import AgentRegistry
from worktogether.core import CollabError, Engine
from worktogether.execution import execute
from worktogether.runtime import Runtime


class DelegatedAnalysisTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.engine = Engine(Path(self.temp.name) / "state.sqlite3")
        self.registry = AgentRegistry(self.engine)
        self.registry.register({"id": "local-analyst", "name": "Local Analyst",
                                "type": "openai_compat", "endpoint": "http://127.0.0.1:11434/v1",
                                "model": "fixture"})
        project = self.engine.project_add("project", self.temp.name)
        self.task_id = self.engine.task_add(project["id"], "feature", "Build a feature") ["id"]
        self.codex = Runtime(self.engine)

    def report(self, dispatch, data=None, status="DONE"):
        report = self.engine.template(self.task_id, dispatch["id"])
        report.update(status=status, summary="Concrete result", basis=["Fixture observation"],
                      next_action="Continue", data=data if data is not None else {})
        return report

    def plan(self, steps=None):
        if steps is None:
            steps = [
                {"id": "research", "title": "Investigate interface", "work_kind": "analysis",
                 "assignee": "local-analyst", "acceptance": ["Document interface"], "depends_on": []},
                {"id": "build", "title": "Implement interface", "work_kind": "code",
                 "assignee": "codex", "acceptance": ["Tests pass"], "depends_on": ["research"]},
            ]
        d = self.engine.dispatch(self.task_id)["dispatch"]
        self.assertEqual(d["kind"], "PLAN")
        self.engine.submit(self.task_id, self.report(d, {"steps": steps}))
        d = self.engine.dispatch(self.task_id)["dispatch"]
        self.assertEqual(d["kind"], "PLAN_REVIEW")
        self.engine.submit(self.task_id, self.report(d))
        d = self.engine.dispatch(self.task_id)["dispatch"]
        self.assertEqual(d["kind"], "PLAN_FINALIZE")
        return d, steps

    def test_discussion_to_delegated_work_and_dependency(self):
        self.engine.message_add(self.task_id, "m1", "local-analyst", "What should we research?")
        chat = self.engine.chat_dispatch(self.task_id)
        self.engine.submit(self.task_id, self.report(chat, {"proposals": [{
            "title": "Investigate interface", "work_kind": "analysis",
            "assignee": "local-analyst", "reason": "Can compare contracts without changing code"}]}))
        plan, steps = self.plan()
        self.assertEqual(plan["payload"]["team_discussion"][0]["question"], "What should we research?")
        self.assertEqual(plan["payload"]["team_discussion"][0]["replies"][0]["proposals"][0]["assignee"],
                         "local-analyst")
        self.assertIn("local-analyst", [a["id"] for a in plan["payload"]["available_agents"]])
        self.engine.submit(self.task_id, self.report(plan, {"steps": steps}))
        approval = self.codex.approval(self.task_id)
        self.codex.approval(self.task_id, approval["digest"])

        analysis = self.engine.dispatch(self.task_id)["dispatch"]
        self.assertEqual((analysis["kind"], analysis["role"]), ("ANALYZE", "local-analyst"))
        self.engine.submit(self.task_id, self.report(analysis, {
            "deliverable": "Use API v2 for reads", "evidence": ["Observed API contract"]}))
        review = self.engine.dispatch(self.task_id)["dispatch"]
        self.assertEqual((review["kind"], review["role"]), ("VERIFY", "antigravity"))
        self.assertEqual(len(review["version"]), 64)
        self.engine.submit(self.task_id, self.report(review, {
            "version": review["version"], "checks": [
                {"criterion": "Document interface", "status": "PASS", "evidence": ["Compared contract"]}],
            "findings": []}, "PASS"))
        coding = self.engine.dispatch(self.task_id)["dispatch"]
        self.assertEqual((coding["kind"], coding["role"]), ("CODE", "codex"))
        self.assertEqual(coding["payload"]["dependency_outputs"][0]["deliverable"],
                         "Use API v2 for reads")

    def test_rejected_analysis_reassigned_with_feedback(self):
        plan, steps = self.plan()
        self.engine.submit(self.task_id, self.report(plan, {"steps": steps}))
        analysis = self.engine.dispatch(self.task_id)["dispatch"]
        self.engine.submit(self.task_id, self.report(analysis, {
            "deliverable": "Wrong answer", "evidence": ["Initial note"]}))
        review = self.engine.dispatch(self.task_id)["dispatch"]
        self.engine.submit(self.task_id, self.report(review, {
            "version": review["version"], "checks": [
                {"criterion": "Document interface", "status": "FAIL", "evidence": ["Wrong version"]}],
            "findings": [{"problem": "Incorrect version", "reproduction": ["Read document"],
                          "expected": "v2", "actual": "v1", "evidence": ["Contract says v2"]}]}, "FAIL"))
        retry = self.engine.dispatch(self.task_id)["dispatch"]
        self.assertEqual((retry["kind"], retry["role"]), ("ANALYZE", "local-analyst"))
        self.assertEqual(retry["payload"]["step"]["repair_count"], 1)
        self.assertEqual(retry["payload"]["step"]["verification"]["status"], "FAIL")

    def test_plan_cannot_assign_code_to_chat_api(self):
        invalid = [{"id": "s1", "title": "Write code", "work_kind": "code",
                    "assignee": "local-analyst", "acceptance": ["Works"], "depends_on": []}]
        plan, _ = self.plan(invalid)
        with self.assertRaises(CollabError) as error:
            self.engine.submit(self.task_id, self.report(plan, {"steps": invalid}))
        self.assertEqual(error.exception.code, "INVALID_PLAN")
        self.assertEqual(self.engine.show(self.task_id)["phase"], "PLAN_FINALIZE")

    def test_approved_auto_execution_routes_api_analysis_then_review(self):
        steps = [{"id": "research", "title": "Investigate", "work_kind": "analysis",
                  "assignee": "local-analyst", "acceptance": ["Document interface"], "depends_on": []}]
        plan, _ = self.plan(steps)
        self.engine.submit(self.task_id, self.report(plan, {"steps": steps}))
        self.codex.approval(self.task_id, self.codex.approval(self.task_id)["digest"])

        engine = self.engine
        task_id = self.task_id
        class Response:
            def __enter__(self): return self
            def __exit__(self, *_): return False
            def read(self, *_):
                active = next(d for d in engine.show(task_id)["dispatches"] if d["status"] == "ACTIVE")
                report = {"schema_version": 1, "task_id": task_id, "dispatch_id": active["id"],
                          "role": "local-analyst", "attempt": 1, "status": "DONE",
                          "summary": "Documented", "basis": ["Local API fixture"],
                          "suggestions": [], "next_action": "Verify",
                          "data": {"deliverable": "Use v2", "evidence": ["Fixture contract"]}}
                return json.dumps({"choices": [{"message": {"content": json.dumps(report)}}]}).encode()

        class Reviewer:
            def run(self, task, dispatch_id, timeout):
                dispatch = next(d for d in engine.show(task)["dispatches"] if d["id"] == dispatch_id)
                assert dispatch["kind"] == "VERIFY"
                report = engine.template(task, dispatch_id)
                report.update(status="PASS", summary="Checked analysis", basis=["Fixture review"],
                              next_action="Complete", data={"version": dispatch["version"],
                              "checks": [{"criterion": "Document interface", "status": "PASS",
                                          "evidence": ["Compared v2 contract"]}], "findings": []})
                engine.submit(task, report)
                return {"status": "COMPLETED"}

        with patch("worktogether.openai_runtime._open_no_redirect", return_value=Response()) as send:
            result = execute(self.engine, self.task_id, self.codex, Reviewer(), max_calls=3)
        self.assertEqual(send.call_count, 1)
        self.assertEqual(result["reason"], "COMPLETED")
        self.assertEqual(self.engine.show(self.task_id)["phase"], "COMPLETED")


if __name__ == "__main__":
    unittest.main()
