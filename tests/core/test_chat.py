import tempfile
import unittest
import threading
from pathlib import Path
from unittest.mock import patch
from worktogether.core import Engine, CollabError, encode
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

    def test_both_two_calls_are_simultaneous_and_do_not_mutate_the_plan(self):
        self.e.message_add(self.t, "m", "both", "How should we approach this?")
        self.assertIsNone(self.e.dispatch(self.t)["dispatch"])
        self.q.once()
        m = self.e.messages(self.t)[0]
        self.assertEqual("DELIVERED", m["status"])
        self.assertEqual({"codex", "antigravity"}, {r["role"] for r in m["replies"]})
        # Round 1 is a true broadcast: both dispatches exist at once and neither
        # agent could see the other's reply before answering.
        chat = [d for d in self.e.show(self.t)["dispatches"] if d["kind"] == "CHAT"]
        self.assertEqual({"codex", "antigravity"}, {d["role"] for d in chat})
        for d in chat:
            self.assertEqual(1, d["payload"]["round"])
            self.assertEqual([], d["payload"]["peer_replies"])
        t = self.e.show(self.t)
        self.assertEqual("PLANNING", t["phase"])
        self.assertIsNone(t["draft_plan"])
        self.assertEqual(2, len(self.c.list(self.t)["runs"]))

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
        self.assertEqual("TASK_PAUSED", self.e.messages(self.t)[0]["wait_reason"])
        self.e.resume(self.t); self.q.once()
        self.assertEqual(1, len(self.c.list(self.t)["runs"]))

    def test_inflight_not_interrupted_and_no_auto_plan(self):
        d = self.e.dispatch(self.t)["dispatch"]
        self.e.message_add(self.t, "m", "codex", "Wait, discuss first")
        self.q.once(); self.assertEqual([], self.c.list(self.t)["runs"])
        self.assertEqual("DISPATCH_UNRESOLVED", self.e.messages(self.t)[0]["wait_reason"])
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

    def test_failed_chat_does_not_lock_other_task_or_retry_old_failure(self):
        self.e.message_add(self.t, "old", "codex", "Old question")
        dispatch = self.e.chat_dispatch(self.t)
        missing = Runtime(self.e, str(Path(self.tmp.name) / "missing-codex"))
        self.assertEqual("FAILED", missing.run(self.t, dispatch["id"])["status"])
        old_state = self.e.show(self.t)
        self.e.message_add(self.t, "queued-old", "codex", "Follow-up")
        self.assertEqual("TASK_BLOCKED", self.e.messages(self.t)[1]["wait_reason"])
        other = self.e.task_add(old_state["project_id"], "New", "Independent task")["id"]
        self.e.message_add(other, "new", "both", "New question")
        self.assertIsNone(self.e.messages(other)[0]["wait_reason"])
        self.q.once(); self.q.once(); self.q.once()
        self.assertEqual("DELIVERED", self.e.messages(other)[0]["status"])
        self.assertEqual(["codex", "antigravity"], [r["role"] for r in self.e.messages(other)[0]["replies"]])
        self.assertEqual(1, len(self.c.list(self.t)["runs"]))
        self.assertEqual(old_state["dispatches"], self.e.show(self.t)["dispatches"])

    def test_same_project_unknown_chat_is_isolated_but_never_replayed(self):
        self.e.message_add(self.t, "old", "codex", "Old question")
        self.e.chat_dispatch(self.t)
        other = self.e.task_add(self.e.show(self.t)["project_id"], "New", "Independent task")["id"]
        self.e.message_add(other, "new", "codex", "New question")
        self.q.once()
        self.assertEqual("DELIVERED", self.e.messages(other)[0]["status"])
        self.assertEqual("RUNNING", self.e.messages(self.t)[0]["status"])
        self.assertEqual([], self.c.list(self.t)["runs"])

    def test_queue_reasons_are_live_and_not_written_into_messages(self):
        self.e.message_add(self.t, "first", "codex", "First")
        self.e.message_add(self.t, "second", "codex", "Second")
        self.assertEqual("EARLIER_MESSAGE", self.e.messages(self.t)[1]["wait_reason"])
        with self.e.transaction() as db:
            db.execute("INSERT INTO runs VALUES(?,?,?,?)", ("unknown", self.t, "unknown-dispatch",
                encode({"status": "PREPARED"})))
        self.assertEqual("RUN_UNRESOLVED", self.e.messages(self.t)[0]["wait_reason"])
        self.assertIsNone(self.e.chat_dispatch(self.t))
        with self.e.transaction() as db:
            self.assertNotIn("wait_reason", db.execute("SELECT body FROM messages WHERE id='first'").fetchone()[0])
            db.execute("DELETE FROM runs WHERE id='unknown'")
        self.assertIsNone(self.e.messages(self.t)[0]["wait_reason"])

    def test_unavailable_recipient_waits_with_reason_without_dispatch(self):
        self.e.message_add(self.t, "m", "codex", "Question")
        with self.e.transaction() as db:
            # Reproduce imported/legacy data with a now-disabled participant.
            db.execute("UPDATE agents SET body=json_set(body,'$.enabled',json('false')) WHERE id='codex'")
        self.assertEqual("AGENT_UNAVAILABLE", self.e.messages(self.t)[0]["wait_reason"])
        self.assertIsNone(self.e.chat_dispatch(self.t))
        self.assertEqual([], self.e.show(self.t)["dispatches"])

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
