"""Tests for Multi-Agent Group Chat Bus and broadcasts."""
import os
import json
import tempfile
import unittest
from unittest.mock import patch

from pathlib import Path
from worktogether.core import Engine, CollabError
from worktogether.agents import AgentRegistry
from worktogether.chat import ChatQueue


class MultiAgentChatTests(unittest.TestCase):
    def setUp(self):
        self.key_patch = patch.dict(os.environ, {"DEEPSEEK_API_KEY": ""})
        self.key_patch.start()
        self.addCleanup(self.key_patch.stop)
        self.temp_dir = tempfile.TemporaryDirectory()
        self.db_path = os.path.join(self.temp_dir.name, "test_chat.db")
        self.engine = Engine(self.db_path)
        self.registry = AgentRegistry(self.engine)
        self.registry.register({"id": "local-model", "name": "Local Model", "type": "openai_compat",
                                "endpoint": "http://127.0.0.1:11434/v1", "model": "test"})
        self.project = self.engine.project_add("Test Chat Proj", self.temp_dir.name)
        self.task = self.engine.task_add(self.project["id"], "Group Discussion", "Multi-agent architecture discussion")
        root = Path(__file__).parent
        self.custom_executables = {
            "codex": str((root / "fake_codex.py").resolve()),
            "antigravity": str((root / "fake_agy.py").resolve()),
        }

    def tearDown(self):
        self.temp_dir.cleanup()

    def test_direct_message_to_custom_agent(self):
        msg = self.engine.message_add(self.task["id"], "msg-1", "local-model", "如何优化多Agent之间的消息同步机制？")
        self.assertEqual(msg["status"], "QUEUED")
        self.assertEqual(msg["recipient"], "local-model")

        d = self.engine.chat_dispatch(self.task["id"])
        self.assertIsNotNone(d)
        self.assertEqual(d["role"], "local-model")
        self.assertEqual(d["payload"]["message"], "如何优化多Agent之间的消息同步机制？")

    def test_broadcast_reaches_every_agent_at_once(self):
        msg = self.engine.message_add(self.task["id"], "msg-all", "all", "请各位Agent分别评估本项目的前端技术选型。")
        self.assertEqual(msg["status"], "QUEUED")
        self.assertEqual(msg["recipient"], "all")

        enabled_agents = msg["participants"]
        self.assertEqual(enabled_agents, ["codex", "antigravity", "local-model"])

        # The whole round is issued in one step: a genuine simultaneous broadcast.
        round_one = self.engine.chat_dispatch_round(self.task["id"])
        self.assertEqual([d["role"] for d in round_one], enabled_agents)
        for d in round_one:
            self.assertEqual(d["payload"]["round"], 1)
            self.assertEqual(d["payload"]["total_rounds"], 1)
            # Nobody sees a peer before answering in round 1.
            self.assertEqual(d["payload"]["peer_replies"], [])
        # A second call while the round is in flight must not double-dispatch.
        self.assertEqual(self.engine.chat_dispatch_round(self.task["id"]), [])

        for d in round_one[:2]:
            runtime = self.registry.get_runtime(d["role"], custom_executables=self.custom_executables)
            self.assertEqual(runtime.run(self.task["id"], d["id"], timeout=30)["status"], "COMPLETED")

        # Still running: the third agent has not answered yet.
        self.assertEqual(self.engine.messages(self.task["id"])[0]["status"], "RUNNING")
        self.assertEqual(self.engine.messages(self.task["id"])[0]["pending_roles"], [enabled_agents[2]])

        last = round_one[2]
        report = {"schema_version": 1, "task_id": self.task["id"], "dispatch_id": last["id"],
                  "role": last["role"], "attempt": 1, "status": "DONE", "summary": "我的独立评估",
                  "basis": ["自己的判断"], "suggestions": [], "next_action": "等待用户", "data": {}}
        class Response:
            def __enter__(self): return self
            def __exit__(self, *_): return False
            def read(self, *_): return json.dumps({"choices": [{"message": {"content": json.dumps(report)}}]}).encode()
        runtime = self.registry.get_runtime(last["role"], custom_executables=self.custom_executables)
        with patch("worktogether.openai_runtime._open_no_redirect", return_value=Response()):
            run3 = runtime.run(self.task["id"], last["id"], timeout=30)
        self.assertEqual(run3["status"], "COMPLETED")

        final_msg = self.engine.messages(self.task["id"])[0]
        self.assertEqual(final_msg["status"], "DELIVERED")
        self.assertEqual(len(final_msg["replies"]), 3)
        self.assertEqual({r["role"] for r in final_msg["replies"]}, set(enabled_agents))
        self.assertTrue(all(r["round"] == 1 for r in final_msg["replies"]))

    def test_second_round_shows_every_peer_reply(self):
        self.engine.message_add(self.task["id"], "msg-r2", "codex,antigravity", "先各自表态，再互相回应。", rounds=2)
        first = self.engine.chat_dispatch_round(self.task["id"])
        self.assertEqual(len(first), 2)
        for d in first:
            runtime = self.registry.get_runtime(d["role"], custom_executables=self.custom_executables)
            self.assertEqual(runtime.run(self.task["id"], d["id"], timeout=30)["status"], "COMPLETED")

        # Round 2 opens automatically and everyone can see the whole first round.
        second = self.engine.chat_dispatch_round(self.task["id"])
        self.assertEqual({d["role"] for d in second}, {"codex", "antigravity"})
        for d in second:
            self.assertEqual(d["payload"]["round"], 2)
            self.assertEqual({r["role"] for r in d["payload"]["peer_replies"]},
                             {"codex", "antigravity"})

    def test_participants_are_fixed_when_message_is_sent(self):
        msg = self.engine.message_add(self.task["id"], "msg-all", "all", "讨论")
        self.assertEqual(msg["participants"], ["codex", "antigravity", "local-model"])
        self.registry.register({"id": "new-model", "name": "New", "type": "openai_compat",
                                "endpoint": "http://127.0.0.1:11434/v1", "model": "test"})
        self.assertEqual(self.engine.messages(self.task["id"])[0]["participants"], msg["participants"])
        with self.assertRaises(CollabError):
            self.registry.remove("local-model")


if __name__ == "__main__":
    unittest.main()
