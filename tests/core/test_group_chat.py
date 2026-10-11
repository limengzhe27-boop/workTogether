"""Tests for the parallel group room: presence, agent-to-agent messages, budget and writes."""
import tempfile
import threading
import unittest
from pathlib import Path

from worktogether.antigravity import AntigravityRuntime
from worktogether.chat import ChatQueue
from worktogether.core import CollabError, Engine
from worktogether.runtime import Runtime


def chat_report(dispatch, summary="回答", data=None, status="DONE"):
    return {
        "schema_version": 1,
        "task_id": dispatch["task_id"],
        "dispatch_id": dispatch["id"],
        "role": dispatch["role"],
        "attempt": dispatch["attempt"],
        "status": status,
        "summary": summary,
        "basis": ["fixture basis"],
        "suggestions": [],
        "next_action": "继续",
        "data": data if data is not None else ({"reason": "blocked"} if status == "BLOCKED" else {}),
    }


class GroupRoomTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        project_dir = Path(self.tmp.name) / "project"
        project_dir.mkdir()
        self.project_dir = project_dir
        self.e = Engine(str(Path(self.tmp.name) / "state.sqlite3"))
        self.project = self.e.project_add("fixture", str(project_dir))
        self.task = self.e.task_add(self.project["id"], "room", "coordinate")["id"]

    def start_selected_room(self, content, speaker="codex"):
        """Both peers are explicitly selected; finish the non-speaking fixture peer."""
        self.e.message_add(self.task, "m", "both", content)
        issued = self.e.chat_dispatch_round(self.task)
        for dispatch in issued:
            if dispatch["role"] != speaker:
                self.e.submit(self.task, chat_report(dispatch))
        return next(d for d in issued if d["role"] == speaker)

    # ---- simultaneous broadcast -------------------------------------------------
    def test_round_one_is_a_simultaneous_broadcast(self):
        self.e.message_add(self.task, "m", "both", "status?")
        issued = self.e.chat_dispatch_round(self.task)
        self.assertEqual({"codex", "antigravity"}, {d["role"] for d in issued})
        for d in issued:
            self.assertEqual([], d["payload"]["peer_replies"])
            self.assertEqual(1, d["payload"]["round"])
            self.assertEqual("user", d["payload"]["message_sender"])
        # Both are in flight at the same moment.
        active = [d for d in self.e.show(self.task)["dispatches"] if d["status"] == "ACTIVE"]
        self.assertEqual(2, len(active))
        for d in issued:
            self.e.submit(self.task, chat_report(d))
        message = self.e.messages(self.task)[0]
        self.assertEqual("DELIVERED", message["status"])
        self.assertEqual({"codex", "antigravity"}, {r["role"] for r in message["replies"]})

    def test_agents_of_one_round_actually_run_concurrently(self):
        root = Path(__file__).parent
        codex = Runtime(self.e, str((root / "fake_codex.py").resolve()))
        queue = ChatQueue(self.e, codex, AntigravityRuntime(self.e, str((root / "fake_agy.py").resolve())))
        barrier = threading.Barrier(2, timeout=5)

        class Held:
            def __init__(self, inner):
                self.inner = inner

            def run(self, task_id, dispatch_id, timeout):
                barrier.wait()
                return self.inner.run(task_id, dispatch_id, timeout)

        queue.providers["codex"] = Held(codex)
        queue.providers["antigravity"] = Held(queue.providers["antigravity"])
        self.e.message_add(self.task, "m", "both", "go")
        queue.once()
        self.assertEqual("DELIVERED", self.e.messages(self.task)[0]["status"])

    # ---- agent-to-agent ---------------------------------------------------------
    def test_agent_can_ask_a_peer_and_the_peer_answers(self):
        dispatch = self.start_selected_room("你是谁")
        self.e.submit(self.task, chat_report(dispatch, data={
            "messages": [{"to": "antigravity", "intent": "ask", "content": "你验证一下这个结论？"}]}))
        messages = self.e.messages(self.task)
        self.assertEqual(2, len(messages))
        auto = messages[1]
        self.assertEqual("codex", auto["sender"])
        self.assertEqual("ask", auto["intent"])
        self.assertEqual("antigravity", auto["recipient"])
        self.assertTrue(auto["auto"])
        self.assertEqual("QUEUED", auto["status"])
        # The addressed peer is the one that gets dispatched, and it knows who spoke.
        follow = self.e.chat_dispatch_round(self.task)
        self.assertEqual(["antigravity"], [d["role"] for d in follow])
        self.assertEqual("codex", follow[0]["payload"]["message_sender"])
        self.assertEqual("ask", follow[0]["payload"]["message_intent"])
        self.assertTrue(follow[0]["payload"]["is_auto"])

    def test_assign_hands_work_to_a_named_peer(self):
        dispatch = self.start_selected_room("改一下")
        report = chat_report(dispatch, data={
            "messages": [{"to": "antigravity", "intent": "assign", "content": "请实现 X"}]})
        self.e.submit(self.task, report)
        auto = self.e.messages(self.task)[1]
        self.assertEqual("assign", auto["intent"])
        self.assertEqual(["antigravity"], auto["participants"])

    def test_claim_and_report_do_not_wait_for_an_answer(self):
        self.e.message_add(self.task, "m", "codex", "谁来做")
        dispatch = self.e.chat_dispatch(self.task)
        self.e.submit(self.task, chat_report(dispatch, data={
            "messages": [{"to": "all", "intent": "claim", "content": "我来做接口层"}]}))
        auto = self.e.messages(self.task)[1]
        self.assertEqual([], auto["participants"])
        self.assertEqual("DELIVERED", auto["status"])
        self.assertEqual(0, self.e.chat_state(self.task)["auto_rounds_used"])

    def test_peer_targets_and_intents_are_validated(self):
        self.e.message_add(self.task, "m", "codex", "hello")
        dispatch = self.e.chat_dispatch(self.task)
        bad_reports = [
            {"messages": [{"to": "ghost", "intent": "ask", "content": "hi"}]},
            {"messages": [{"to": "codex", "intent": "ask", "content": "self"}]},
            {"messages": [{"to": "antigravity", "intent": "explode", "content": "hi"}]},
            {"messages": [{"to": "antigravity", "intent": "ask", "content": ""}]},
            {"messages": [{"to": "antigravity", "intent": "ask", "content": "x"}] * 4},
            {"unexpected": True},
        ]
        for data in bad_reports:
            with self.subTest(data=data):
                with self.assertRaises(CollabError):
                    self.e.submit(self.task, chat_report(dispatch, data=data))

    def test_a_peer_outside_the_room_is_refused(self):
        self.e.message_add(self.task, "m", "codex", "hello")
        dispatch = self.e.chat_dispatch(self.task)
        # deepseek-harness is disabled, so it is not a member of the room.
        with self.assertRaises(CollabError):
            self.e.submit(self.task, chat_report(dispatch, data={
                "messages": [{"to": "deepseek-harness", "intent": "assign", "content": "外部代理"}]}))
        self.assertEqual(1, len(self.e.messages(self.task)))

    def test_a_peer_that_becomes_unavailable_can_still_be_skipped(self):
        self.e.chat_config(self.task, {"max_auto_rounds": 4})
        dispatch = self.start_selected_room("开始")
        self.e.submit(self.task, chat_report(dispatch, data={
            "messages": [{"to": "antigravity", "intent": "ask", "content": "请复核"}]}))
        # The addressed peer is disabled before the room can hand it the turn.
        # Reproduce imported/legacy state directly: the registry refuses to
        # disable an agent with an unfinished conversation, by design.
        with self.e.transaction() as db:
            db.execute("UPDATE agents SET body=json_set(body,'$.enabled',json('false')) WHERE id='antigravity'")
        self.assertEqual([], self.e.chat_dispatch_round(self.task))
        auto = self.e.messages(self.task)[1]
        self.assertEqual("QUEUED", auto["status"])
        self.assertEqual("AGENT_UNAVAILABLE", auto["wait_reason"])
        # Otherwise the room would sit on this message forever with no way out.
        resolved = self.e.message_resolve(self.task, auto["id"], action="skip")
        self.assertEqual("DELIVERED", resolved["status"])

    def test_session_config_rejects_unknown_keys(self):
        with self.assertRaises(CollabError):
            self.e.chat_config(self.task, {"timeout_seconds": 30})

    # ---- automatic round budget -------------------------------------------------
    def test_automatic_back_and_forth_stops_at_the_budget(self):
        self.e.chat_config(self.task, {"max_auto_rounds": 1})
        dispatch = self.start_selected_room("开始")
        self.e.submit(self.task, chat_report(dispatch, data={
            "messages": [{"to": "antigravity", "intent": "ask", "content": "第一轮"}]}))
        follow = self.e.chat_dispatch_round(self.task)[0]
        self.e.submit(self.task, chat_report(follow, data={
            "messages": [{"to": "codex", "intent": "ask", "content": "第二轮"}]}))
        messages = self.e.messages(self.task)
        self.assertEqual("HELD", messages[-1]["status"])
        self.assertTrue(messages[-1]["held"])
        self.assertEqual(1, self.e.chat_state(self.task)["held"])
        # A held message is never dispatched automatically.
        self.assertEqual([], self.e.chat_dispatch_round(self.task))

        released = self.e.chat_release(self.task)
        self.assertEqual(1, released["released"])
        self.assertEqual("QUEUED", self.e.messages(self.task)[-1]["status"])
        self.assertTrue(self.e.chat_dispatch_round(self.task))

    def test_an_agent_that_declines_is_not_shown_as_a_normal_answer(self):
        self.e.message_add(self.task, "m", "both", "能做吗")
        issued = self.e.chat_dispatch_round(self.task)
        declined = next(d for d in issued if d["role"] == "codex")
        accepted = next(d for d in issued if d["role"] == "antigravity")
        self.e.submit(self.task, chat_report(declined, status="BLOCKED", data={"reason": "需要新授权"}))
        self.e.submit(self.task, chat_report(accepted, summary="可以，我来做"))
        message = self.e.messages(self.task)[0]
        self.assertEqual("BLOCKED", message["status"])
        self.assertEqual(["codex"], message["failed_roles"])
        self.assertIn("需要新授权", message["error"])
        # The peer's answer survives, and an explicit skip recovers the room.
        resolved = self.e.message_resolve(self.task, "m", action="skip")
        self.assertEqual("DELIVERED", resolved["status"])
        self.assertEqual(["antigravity"], [r["role"] for r in resolved["replies"] if not r.get("skipped")])
        self.assertEqual("ACTIVE", self.e.show(self.task)["status"])

    # ---- write serialization ----------------------------------------------------
    def test_only_the_capable_coder_holds_the_write_turn(self):
        self.e.chat_config(self.task, {"allow_write": True})
        self.e.message_add(self.task, "m", "both", "实现它")
        issued = self.e.chat_dispatch_round(self.task)
        writers = [d["role"] for d in issued if d["payload"].get("allow_write")]
        self.assertEqual(["codex"], writers)
        readers = [d["role"] for d in issued if not d["payload"].get("allow_write")]
        self.assertEqual(["antigravity"], readers)

    def test_a_writable_agent_gets_the_turn_when_it_is_the_only_one_addressed(self):
        self.e.chat_config(self.task, {"allow_write": True})
        # Every local CLI adapter can write now, so an explicit hand-off to a
        # non-coder must actually grant that agent the turn.
        self.e.message_add(self.task, "m", "antigravity", "改一下")
        issued = self.e.chat_dispatch_round(self.task)
        self.assertEqual(["antigravity"], [d["role"] for d in issued])
        self.assertTrue(issued[0]["payload"].get("allow_write"))

    def test_a_discussion_round_still_writes_through_the_coder(self):
        self.e.chat_config(self.task, {"allow_write": True})
        self.e.message_add(self.task, "m", "both", "一起看看")
        issued = self.e.chat_dispatch_round(self.task)
        writers = [d["role"] for d in issued if d["payload"].get("allow_write")]
        self.assertEqual(["codex"], writers)

    def test_an_assignment_inherits_the_authorized_chain_write_grant(self):
        self.e.chat_config(self.task, {"allow_write": True})
        speaker = self.start_selected_room("先看看这个项目", speaker="antigravity")
        self.e.submit(self.task, chat_report(speaker, data={
            "messages": [{"to": "codex", "intent": "assign", "content": "请把占位测试换成真实测试"}]}))
        auto = [m for m in self.e.messages(self.task) if m.get("auto")][-1]
        self.assertEqual("assign", auto["intent"])
        self.assertTrue(auto.get("allow_write"))
        follow = self.e.chat_dispatch_round(self.task)
        self.assertEqual(["codex"], [d["role"] for d in follow])
        self.assertTrue(follow[0]["payload"].get("allow_write"))

    def test_an_assignment_stays_read_only_when_the_session_forbids_writes(self):
        self.e.chat_config(self.task, {"allow_write": False})
        speaker = self.start_selected_room("先看看", speaker="antigravity")
        self.assertFalse(speaker["payload"].get("allow_write"))
        self.e.submit(self.task, chat_report(speaker, data={
            "messages": [{"to": "codex", "intent": "assign", "content": "请改"}]}))
        auto = [m for m in self.e.messages(self.task) if m.get("auto")][-1]
        self.assertFalse(auto.get("allow_write"))
        follow = self.e.chat_dispatch_round(self.task)
        self.assertEqual(["codex"], [d["role"] for d in follow])
        self.assertFalse(follow[0]["payload"].get("allow_write"))

    def test_a_second_task_in_the_same_project_stays_read_only_while_one_writes(self):
        other = self.e.task_add(self.project["id"], "other", "parallel")["id"]
        self.e.chat_config(self.task, {"allow_write": True})
        self.e.chat_config(other, {"allow_write": True})
        self.e.message_add(self.task, "m1", "codex", "先动手")
        first = self.e.chat_dispatch_round(self.task)
        self.assertTrue(first[0]["payload"].get("allow_write"))
        # The first agent is still holding the project write turn.
        self.e.message_add(other, "m2", "codex", "我也要写")
        second = self.e.chat_dispatch_round(other)
        self.assertTrue(second)
        self.assertFalse(second[0]["payload"].get("allow_write"))

    def test_session_write_grant_is_configurable(self):
        self.assertFalse(self.e.chat_state(self.task)["allow_write"])
        self.e.chat_config(self.task, {"allow_write": True})
        self.assertTrue(self.e.chat_state(self.task)["allow_write"])
        self.e.chat_config(self.task, {"allow_write": False})
        self.assertFalse(self.e.chat_state(self.task)["allow_write"])
        with self.assertRaises(CollabError):
            self.e.chat_config(self.task, {"allow_write": "yes"})
        with self.assertRaises(CollabError):
            self.e.chat_config(self.task, {"max_auto_rounds": 99})

    def test_sessions_missing_write_config_do_not_acquire_permission(self):
        with self.e.transaction() as db:
            t = self.e._task(db, self.task)
            t["config"] = {"max_repairs": 2, "timeout_seconds": 1800}
            self.e._save(db, t, "LEGACY_FIXTURE", {})
        state = self.e.chat_state(self.task)
        self.assertFalse(state["allow_write"])
        self.assertEqual(2, state["max_auto_rounds"])
        self.e.message_add(self.task, "m", "both", "写吧")
        issued = self.e.chat_dispatch_round(self.task)
        self.assertEqual([], [d["role"] for d in issued if d["payload"].get("allow_write")])


if __name__ == "__main__":
    unittest.main()
