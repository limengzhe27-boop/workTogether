import tempfile
import threading
import unittest
from unittest.mock import patch
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from worktogether.core import CollabError, Engine
from worktogether.runtime import Runtime
from worktogether.antigravity import AntigravityRuntime
from worktogether.teamwork import discuss_team
from worktogether.execution import execute


class TeamworkTests(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.engine = Engine(Path(tmp.name) / "state.db")
        project = self.engine.project_add("Project", tmp.name)
        self.task = self.engine.task_add(project["id"], "Task", "Implement feature")["id"]
        root = Path(__file__).parent
        self.codex = Runtime(self.engine, str(root / "fake_codex.py"))
        self.agy = AntigravityRuntime(self.engine, str(root / "fake_agy.py"))
        self.engine.configure_team(self.task, ["codex", "antigravity"], "antigravity", "codex")

    def test_parallel_rounds_converge_then_execute(self):
        barrier = threading.Barrier(2)
        arrivals = []

        class Observed:
            def __init__(self, runtime):
                self.runtime = runtime

            def run(inner, task, dispatch_id, timeout):
                dispatch = next(d for d in self.engine.show(task)["dispatches"] if d["id"] == dispatch_id)
                if dispatch["kind"] in ("CONSULT", "CLAIM"):
                    arrivals.append((dispatch["kind"], dispatch["role"]))
                    barrier.wait(timeout=5)  # Fails if orchestration is accidentally serial.
                return inner.runtime.run(task, dispatch_id, timeout)

        result = discuss_team(self.engine, self.task, Observed(self.codex), Observed(self.agy))
        self.assertEqual("PLANNING_FINISHED", result["reason"])
        self.assertEqual(7, len(result["runs"]))
        self.assertEqual(4, len(arrivals))
        task = self.engine.show(self.task)
        self.assertEqual("codex", task["steps"][0]["assignee"])
        self.assertEqual("antigravity", task["steps"][0]["verifier"])
        self.assertIn("Member claim", task["steps"][0]["assignment_reason"])
        draft = next(d for d in task["dispatches"] if d["kind"] == "PLAN")
        self.assertEqual(2, len(draft["payload"]["team_inputs"]))
        final = next(d for d in task["dispatches"] if d["kind"] == "PLAN_FINALIZE")
        self.assertEqual(4, len(final["payload"]["team_inputs"]))
        self.assertEqual([], discuss_team(self.engine, self.task, self.codex, self.agy)["runs"])
        digest = self.codex.approval(self.task)["digest"]
        self.codex.approval(self.task, digest)
        self.assertEqual("COMPLETED", execute(self.engine, self.task, self.codex, self.agy)["reason"])

    def test_atomic_round_dedup_and_unknown_work_not_replayed(self):
        def issue():
            try:
                return self.engine.team_round(self.task, "CONSULT")
            except CollabError:
                return []
        with ThreadPoolExecutor(max_workers=2) as pool:
            results = list(pool.map(lambda _: issue(), range(2)))
        self.assertEqual(2, sum(map(len, results)))
        self.assertEqual("EXISTING_DISPATCH", discuss_team(
            self.engine, self.task, self.codex, self.agy)["reason"])
        self.assertEqual([], self.codex.list(self.task)["runs"])

    def test_manual_dispatch_cannot_skip_team_input(self):
        self.assertEqual("TEAM_ROUND_REQUIRED", self.engine.dispatch(self.task)["reason"])

    def test_other_tasks_failed_chat_does_not_block_team_round(self):
        other = self.engine.task_add(self.engine.show(self.task)["project_id"], "Old", "Old task")["id"]
        self.engine.message_add(other, "old", "codex", "Old question")
        dispatch = self.engine.chat_dispatch(other)
        with patch.dict("os.environ", {"WT_FAKE_MODE": "exit"}):
            self.codex.run(other, dispatch["id"])
        self.assertEqual("BLOCKED", self.engine.show(other)["status"])
        self.assertEqual({"codex", "antigravity"},
                         {d["role"] for d in self.engine.team_round(self.task, "CONSULT")})

    def test_pause_and_revision_do_not_reuse_old_opinions(self):
        for d in self.engine.team_round(self.task, "CONSULT"):
            provider = self.codex if d["role"] == "codex" else self.agy
            provider.run(self.task, d["id"])
        self.engine.pause(self.task)
        self.assertEqual("TASK_NOT_ACTIVE", discuss_team(self.engine, self.task, self.codex, self.agy)["reason"])
        self.engine.revise(self.task, "revision", 1, "Different feature")
        self.engine.resume(self.task)
        result = discuss_team(self.engine, self.task, self.codex, self.agy)
        self.assertEqual(7, len(result["runs"]))
        self.assertEqual("PLANNING_FINISHED", result["reason"])
