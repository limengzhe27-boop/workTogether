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

    def test_broadcast_to_all_agents(self):
        msg = self.engine.message_add(self.task["id"], "msg-all", "all", "请各位Agent分别评估本项目的前端技术选型。")
        self.assertEqual(msg["status"], "QUEUED")
        self.assertEqual(msg["recipient"], "all")

        enabled_agents = msg["participants"]
        self.assertEqual(enabled_agents, ["codex", "antigravity", "local-model"])

        # 1. First agent dispatch
        d1 = self.engine.chat_dispatch(self.task["id"])
        self.assertIsNotNone(d1)
        role1 = d1["role"]
        self.assertEqual(role1, enabled_agents[0])

        # Submit role 1 response
        r1 = self.registry.get_runtime(role1, custom_executables=self.custom_executables)
        run1 = r1.run(self.task["id"], d1["id"], timeout=30)
        self.assertEqual(run1["status"], "COMPLETED")

        # 2. Second agent dispatch (sees role 1 peer reply)
        d2 = self.engine.chat_dispatch(self.task["id"])
        self.assertIsNotNone(d2)
        role2 = d2["role"]
        self.assertEqual(role2, enabled_agents[1])
        self.assertEqual(len(d2["payload"]["peer_replies"]), 1)
        self.assertEqual(d2["payload"]["peer_replies"][0]["role"], role1)

        # Submit role 2 response
        r2 = self.registry.get_runtime(role2, custom_executables=self.custom_executables)
        run2 = r2.run(self.task["id"], d2["id"], timeout=30)
        self.assertEqual(run2["status"], "COMPLETED")

        # 3. Third agent dispatch (sees role 1 & 2 peer replies)
        d3 = self.engine.chat_dispatch(self.task["id"])
        self.assertIsNotNone(d3)
        role3 = d3["role"]
        self.assertEqual(role3, enabled_agents[2])
        self.assertEqual(len(d3["payload"]["peer_replies"]), 2)

        # Submit role 3 response
        r3 = self.registry.get_runtime(role3, custom_executables=self.custom_executables)
        report = {"schema_version": 1, "task_id": self.task["id"], "dispatch_id": d3["id"],
                  "role": role3, "attempt": 1, "status": "DONE", "summary": "综合前两位意见",
                  "basis": ["前两位的回答"], "suggestions": [], "next_action": "等待用户", "data": {}}
        class Response:
            def __enter__(self): return self
            def __exit__(self, *_): return False
            def read(self, *_): return json.dumps({"choices": [{"message": {"content": json.dumps(report)}}]}).encode()
        with patch("urllib.request.urlopen", return_value=Response()):
            run3 = r3.run(self.task["id"], d3["id"], timeout=30)
        self.assertEqual(run3["status"], "COMPLETED")

        # Check final message status is DELIVERED
        msgs = self.engine.messages(self.task["id"])
        self.assertEqual(len(msgs), 1)
        final_msg = msgs[0]
        self.assertEqual(final_msg["status"], "DELIVERED")
        self.assertEqual(len(final_msg["replies"]), 3)
        reply_roles = [r["role"] for r in final_msg["replies"]]
        self.assertEqual(reply_roles, [role1, role2, role3])

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
