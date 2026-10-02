import tempfile
import unittest
import threading
from pathlib import Path
from unittest.mock import patch
from worktogether.core import Engine, CollabError
from worktogether.runtime import Runtime
from worktogether.antigravity import AntigravityRuntime
from worktogether.chat import ChatQueue
from worktogether.discussion import discuss


class ChatTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(); self.addCleanup(self.tmp.cleanup)
        self.e = Engine(Path(self.tmp.name) / "state.sqlite3")
        p = self.e.project_add("test", self.tmp.name)
        self.t = self.e.task_add(p["id"], "test", "original requirement")["id"]
        root = Path(__file__).parent
        self.c = Runtime(self.e, str((root / "fake_codex.py").resolve()))
        self.a = AntigravityRuntime(self.e, str((root / "fake_agy.py").resolve()))
        self.q = ChatQueue(self.e, self.c, self.a)

    def test_both_two_calls_peer_context_no_plan_mutation(self):
        self.e.message_add(self.t, "m", "both", "How should we approach this?")
        self.assertIsNone(self.e.dispatch(self.t)["dispatch"])
        self.q.once(); self.q.once(); self.q.once()
        m = self.e.messages(self.t)[0]
        self.assertEqual("DELIVERED", m["status"])
        self.assertEqual(["codex", "antigravity"], [r["role"] for r in m["replies"]])
        t = self.e.show(self.t)
        self.assertEqual("PLANNING", t["phase"])
        self.assertIsNone(t["draft_plan"])
        self.assertEqual(2, len(self.c.list(self.t)["runs"]))
        self.assertEqual("codex", t["dispatches"][1]["payload"]["peer_replies"][0]["role"])

    def test_exact_recipient_and_session_resume(self):
        for i in range(2):
            self.e.message_add(self.t, str(i), "antigravity", "Question")
            self.q.once()
        runs = self.c.list(self.t)["runs"]
        self.assertEqual(["antigravity"] * 2, [r["provider"] for r in runs])
        self.assertEqual(runs[0]["session_id"], runs[1]["session_id"])

    def test_paused_queue_and_dedup(self):
        self.e.pause(self.t)
        self.e.message_add(self.t, "m", "codex", "question")
        self.e.message_add(self.t, "m", "codex", "question")
        with self.assertRaises(CollabError): self.e.message_add(self.t, "m", "codex", "changed")
        self.q.once()
        self.assertEqual([], self.c.list(self.t)["runs"])
        self.e.resume(self.t); self.q.once()
        self.assertEqual(1, len(self.c.list(self.t)["runs"]))

    def test_inflight_not_interrupted_and_no_auto_plan(self):
        d = self.e.dispatch(self.t)["dispatch"]
        self.e.message_add(self.t, "m", "codex", "Wait, discuss first")
        self.q.once(); self.assertEqual([], self.c.list(self.t)["runs"])
        self.c.run(self.t, d["id"]); self.q.once()
        self.assertEqual("PLAN_REVIEW", self.e.show(self.t)["phase"])
        self.assertEqual("DELIVERED", self.e.messages(self.t)[0]["status"])

    def test_revision_stale_message_requires_review(self):
        self.e.message_add(self.t, "m", "codex", "old question")
        self.e.revise(self.t, "rev", 1, "new requirement")
        self.q.once()
        self.assertEqual("NEEDS_REVIEW", self.e.messages(self.t)[0]["status"])
        self.assertEqual([], self.c.list(self.t)["runs"])

    def test_failure_no_auto_retry(self):
        self.e.message_add(self.t, "m", "codex", "question")
        with patch.dict("os.environ", {"WT_FAKE_MODE": "exit"}): self.q.once()
        self.q.once()
        self.assertEqual("BLOCKED", self.e.messages(self.t)[0]["status"])
        self.assertEqual(1, len(self.c.list(self.t)["runs"]))

    def test_discussion_yields_to_queued_message(self):
        self.e.message_add(self.t, "m", "antigravity", "question")
        result = discuss(self.e, self.t, self.c, self.a)
        self.assertEqual("MESSAGE_PENDING", result["reason"])
        self.assertEqual([], result["runs"])

    def test_task_isolation(self):
        p = self.e.project_add("another", self.tmp.name)
        other = self.e.task_add(p["id"], "other", "other")["id"]
        self.e.message_add(self.t, "m", "codex", "private")
        self.assertEqual([], self.e.messages(other))
        self.q.once()
        self.e.message_add(other, "m", "codex", "different")
        self.q.once()
        self.assertNotEqual(self.c.list(self.t)["runs"][0]["session_id"], self.c.list(other)["runs"][0]["session_id"])

    def test_unknown_claim_is_not_replayed(self):
        self.e.message_add(self.t, "m", "both", "question")
        d = self.e.chat_dispatch(self.t)
        self.assertIsNotNone(d)
        self.q.once()
        self.assertEqual([], self.c.list(self.t)["runs"])
        self.assertEqual("RUNNING", self.e.messages(self.t)[0]["status"])

    def test_completed_task_can_discuss_without_reopening_development(self):
        with self.e.transaction() as db:
            t = self.e._task(db, self.t)
            t.update(phase="COMPLETED", status="COMPLETED")
            self.e._save(db, t, "TEST_COMPLETE", {})
        self.e.message_add(self.t, "m", "codex", "Explain the result")
        self.q.once()
        self.assertEqual("DELIVERED", self.e.messages(self.t)[0]["status"])
        self.assertEqual("COMPLETED", self.e.show(self.t)["status"])

    def test_explicit_retry_preserves_antigravity_role(self):
        self.e.message_add(self.t, "m", "antigravity", "question")
        with patch.dict("os.environ", {"WT_AGY_FIXTURE": "waiting"}): self.q.once()
        d = self.e.show(self.t)["dispatches"][0]
        retried = self.e.retry(self.t, d["id"])["dispatch"]
        self.assertEqual("antigravity", retried["role"])
        self.a.run(self.t, retried["id"])
        self.assertEqual("DELIVERED", self.e.messages(self.t)[0]["status"])

    def test_background_tasks_overlap_without_duplicate_workers(self):
        other_dir = Path(self.tmp.name) / "other-project"
        other_dir.mkdir()
        p = self.e.project_add("independent", str(other_dir))
        other = self.e.task_add(p["id"], "other", "independent")["id"]
        self.e.message_add(self.t, "one", "codex", "Discuss")
        self.e.message_add(other, "two", "codex", "Discuss independently")
        barrier = threading.Barrier(3)
        release = threading.Event()
        runtime = self.c

        class HeldProvider:
            def run(inner, task_id, dispatch_id, timeout):
                barrier.wait(timeout=5)
                if not release.wait(5):
                    raise TimeoutError("Fixture never released")
                return runtime.run(task_id, dispatch_id, timeout)

        self.q.providers["codex"] = HeldProvider()
        self.assertEqual(2, self.q.background_once())
        try:
            barrier.wait(timeout=5)
            self.assertEqual(0, self.q.background_once())
        finally:
            release.set()
            for worker in self.q._workers.values():
                worker.join(timeout=5)
        self.assertEqual("DELIVERED", self.e.messages(self.t)[0]["status"])
        self.assertEqual("DELIVERED", self.e.messages(other)[0]["status"])
        self.assertEqual(1, len(runtime.list(self.t)["runs"]))
        self.assertEqual(1, len(runtime.list(other)["runs"]))
        self.q.stopped.set()
        self.assertEqual(0, self.q.background_once())
