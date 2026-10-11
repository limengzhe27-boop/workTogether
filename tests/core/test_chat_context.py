"""Tests for chat-scoped project context, message options and report normalization."""
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from worktogether.antigravity import AntigravityRuntime
from worktogether.chat import ChatQueue
from worktogether.core import CollabError, Engine
from worktogether.runtime import Runtime, normalize_chat_report


class ChatContextTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.project_dir = Path(self.tmp.name) / "project"
        self.project_dir.mkdir()
        (self.project_dir / "README.md").write_text("# Widget\n\nBuilds widgets.\n", encoding="utf-8")
        (self.project_dir / "main.py").write_text("print('widget')\n", encoding="utf-8")
        self.e = Engine(str(Path(self.tmp.name) / "state.sqlite3"))
        project = self.e.project_add("widgets", str(self.project_dir))
        self.project_id = project["id"]
        self.t = self.e.task_add(self.project_id, "chat", "talk about the project")["id"]
        root = Path(__file__).parent
        self.codex = Runtime(self.e, str((root / "fake_codex.py").resolve()))
        self.agy = AntigravityRuntime(self.e, str((root / "fake_agy.py").resolve()))
        self.q = ChatQueue(self.e, self.codex, self.agy)

    def dispatch_payload(self):
        dispatch = self.e.chat_dispatch(self.t)
        self.assertIsNotNone(dispatch)
        return dispatch["payload"]

    def test_every_chat_dispatch_carries_project_orientation(self):
        self.e.message_add(self.t, "m", "codex", "这个项目是做什么的？")
        payload = self.dispatch_payload()
        context = payload["project_context"]
        self.assertEqual("project", context["name"])
        self.assertIn("Builds widgets.", context["readme"])
        self.assertIn("main.py", context["files"])

    def test_project_memory_is_injected_when_present(self):
        self.e.project_memory_update(self.project_id, "Maintain Python 3.9 compatibility.")
        self.e.message_add(self.t, "m", "codex", "constraints?")
        self.assertEqual("Maintain Python 3.9 compatibility.", self.dispatch_payload()["project_memory"])

    def test_allow_write_flows_into_the_dispatch_payload(self):
        self.e.message_add(self.t, "m", "codex", "add a docstring", allow_write=True)
        self.assertTrue(self.dispatch_payload()["allow_write"])

    def test_write_is_granted_by_default_and_only_to_one_agent(self):
        # A session is writable by default; parallel discussion still serializes
        # the write turn so two agents never edit the same checkout at once.
        self.e.message_add(self.t, "m", "both", "please look at the code")
        issued = self.e.chat_dispatch_round(self.t)
        writers = [d["role"] for d in issued if d["payload"].get("allow_write")]
        self.assertEqual(["codex"], writers)
        self.assertEqual(1, len(writers))

    def test_write_can_be_turned_off_for_the_session(self):
        self.e.chat_config(self.t, {"allow_write": False})
        self.e.message_add(self.t, "m", "both", "just discuss")
        issued = self.e.chat_dispatch_round(self.t)
        self.assertTrue(issued)
        self.assertFalse(any(d["payload"].get("allow_write") for d in issued))

    def test_referenced_files_are_attached_within_bounds(self):
        self.e.message_add(self.t, "m", "codex", "review main", context_files=["main.py"])
        attached = self.dispatch_payload()["attached_files"]
        self.assertEqual(["main.py"], [item["path"] for item in attached])
        self.assertIn("widget", attached[0]["content"])

    def test_referenced_session_excerpts_are_attached(self):
        other = self.e.task_add(self.project_id, "previous", "earlier discussion")["id"]
        self.e.message_add(other, "old", "codex", "earlier question")
        self.q.once()
        self.e.message_add(self.t, "m", "codex", "follow up", referenced_task_ids=[other])
        referenced = self.dispatch_payload()["referenced_sessions"]
        self.assertEqual("previous", referenced[0]["title"])
        self.assertIn("codex", referenced[0]["summary"])

    def test_rounds_keep_the_roster_and_add_a_second_pass(self):
        message = self.e.message_add(self.t, "m", "both", "debate this", rounds=2)
        # The roster is the roster; rounds no longer duplicate participants.
        self.assertEqual(["codex", "antigravity"], message["participants"])
        self.assertEqual(2, message["rounds"])
        self.assertEqual(1, message["round"])

    def test_message_options_are_validated(self):
        for kwargs in ({"rounds": 3}, {"rounds": "2"}, {"allow_write": "yes"},
                       {"context_files": ["a"] * 11}, {"referenced_task_ids": ["a"] * 6},
                       {"context_files": [1]}):
            with self.subTest(**kwargs):
                with self.assertRaises(CollabError):
                    self.e.message_add(self.t, "m", "codex", "hello", **kwargs)

    def test_comma_separated_recipients_must_be_ready(self):
        message = self.e.message_add(self.t, "m", "codex,antigravity", "team")
        self.assertEqual(["codex", "antigravity"], message["participants"])
        with self.assertRaises(CollabError):
            self.e.message_add(self.t, "m2", "codex,ghost", "team")
        with self.assertRaises(CollabError):
            self.e.message_add(self.t, "m3", "codex,codex", "team")

    def test_failed_reply_can_be_skipped_explicitly(self):
        self.e.message_add(self.t, "m", "codex", "question")
        with patch.dict("os.environ", {"WT_FAKE_MODE": "exit"}):
            self.q.once()
        self.assertEqual("BLOCKED", self.e.messages(self.t)[0]["status"])
        resolved = self.e.message_resolve(self.t, "m", action="skip")
        self.assertEqual("DELIVERED", resolved["status"])
        self.assertTrue(resolved["replies"][0]["skipped"])
        self.assertIsNone(resolved.get("error"))

    def test_partial_round_failure_can_be_skipped_without_losing_peers(self):
        # Only codex is broken; antigravity runs fine in the same parallel round.
        import os
        self.e.message_add(self.t, "m", "both", "question")
        with patch.dict("os.environ", {"WT_FAKE_MODE": "exit"}):
            self.q.once()
        message = self.e.messages(self.t)[0]
        self.assertEqual("BLOCKED", message["status"])
        self.assertEqual("codex", message["blocked_role"])
        resolved = self.e.message_resolve(self.t, "m", action="skip")
        self.assertEqual("DELIVERED", resolved["status"])
        by_role = {r["role"]: r for r in resolved["replies"]}
        self.assertTrue(by_role["codex"]["skipped"])
        self.assertFalse(by_role["antigravity"].get("skipped", False))

    def test_retry_requeues_the_message(self):
        self.e.message_add(self.t, "m", "codex", "question")
        with patch.dict("os.environ", {"WT_FAKE_MODE": "exit"}):
            self.q.once()
        retried = self.e.message_resolve(self.t, "m", action="retry")
        self.assertEqual("QUEUED", retried["status"])

    def test_resolve_rejects_unknown_messages_and_actions(self):
        with self.assertRaises(CollabError):
            self.e.message_resolve(self.t, "ghost", action="skip")
        self.e.message_add(self.t, "m", "codex", "question")
        with self.assertRaises(CollabError):
            self.e.message_resolve(self.t, "m", action="explode")


class NormalizeChatReportTests(unittest.TestCase):
    dispatch = {"kind": "CHAT", "task_id": "t", "id": "d", "role": "codex", "attempt": 1}

    def normalize(self, raw):
        return normalize_chat_report(raw, self.dispatch)

    def test_plain_markdown_becomes_a_valid_envelope(self):
        report = self.normalize("## 结论\n\n直接回答。")
        self.assertEqual("DONE", report["status"])
        self.assertIn("直接回答", report["summary"])
        self.assertTrue(report["basis"])
        self.assertEqual({}, report["data"])

    def test_fenced_json_is_unwrapped(self):
        report = self.normalize('```json\n{"summary": "ok", "status": "DONE"}\n```')
        self.assertEqual("ok", report["summary"])

    def test_embedded_json_object_is_detected(self):
        report = self.normalize('Here you go: {"summary": "extracted", "basis": ["a"]} thanks')
        self.assertEqual("extracted", report["summary"])
        self.assertEqual(["a"], report["basis"])

    def test_blocked_report_requires_a_reason(self):
        report = self.normalize({"status": "BLOCKED", "summary": "cannot proceed"})
        self.assertEqual("BLOCKED", report["status"])
        self.assertEqual({"reason": "cannot proceed"}, report["data"])

    def test_invalid_basis_and_suggestions_are_repaired(self):
        report = self.normalize({"summary": "x", "basis": [], "suggestions": ["a", "b", "c", "d"]})
        self.assertTrue(report["basis"])
        self.assertEqual(3, len(report["suggestions"]))

    def test_non_chat_reports_pass_through_untouched(self):
        raw = {"anything": 1}
        self.assertIs(raw, normalize_chat_report(raw, {"kind": "CODE"}))

    def test_peer_messages_survive_normalization(self):
        report = self.normalize({"summary": "hi", "data": {
            "messages": [{"to": "antigravity", "intent": "ask", "content": "复核一下"}]}})
        self.assertEqual([{"to": "antigravity", "intent": "ask", "content": "复核一下"}],
                         report["data"]["messages"])

    def test_malformed_chat_data_is_dropped(self):
        for data in ({"nope": 1}, {"messages": [{"to": "x"}]}, {"messages": "not a list"}):
            with self.subTest(data=data):
                self.assertEqual({}, self.normalize({"summary": "hi", "data": data})["data"])

    def test_proposals_and_messages_can_coexist(self):
        report = self.normalize({"summary": "hi", "data": {
            "proposals": [{"title": "t", "work_kind": "code", "assignee": "codex", "reason": "r"}],
            "messages": [{"to": "antigravity", "intent": "report", "content": "done"}]}})
        self.assertIn("proposals", report["data"])
        self.assertIn("messages", report["data"])

    def test_empty_response_is_rejected(self):
        with self.assertRaises(ValueError):
            self.normalize("   ")


if __name__ == "__main__":
    unittest.main()
