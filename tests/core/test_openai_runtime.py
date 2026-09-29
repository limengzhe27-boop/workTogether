"""Tests for OpenAICompatibleRuntime with DeepSeek Hermes and custom models."""
import json
import os
import tempfile
import unittest
from unittest.mock import patch

from worktogether.core import Engine, CollabError
from worktogether.agents import AgentRegistry
from worktogether.openai_runtime import OpenAICompatibleRuntime


class OpenAIRuntimeTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.db_path = os.path.join(self.temp_dir.name, "test_openai.db")
        self.engine = Engine(self.db_path)
        self.registry = AgentRegistry(self.engine)
        self.project = self.engine.project_add("Test Proj", self.temp_dir.name)
        self.task = self.engine.task_add(self.project["id"], "Chat Task", "Req")

        self.registry.register({"id": "local-model", "name": "Local Model", "type": "openai_compat",
                                "endpoint": "http://127.0.0.1:11434/v1", "model": "test"})
        self.agent_cfg = self.registry.get("local-model")
        self.runtime = OpenAICompatibleRuntime(self.engine, self.agent_cfg)

    def tearDown(self):
        self.temp_dir.cleanup()

    def test_local_chat_dispatch_execution_with_real_protocol_shape(self):
        msg = self.engine.message_add(self.task["id"], "msg-1", "local-model", "请问如何设计架构？")
        self.assertEqual(msg["status"], "QUEUED")

        d = self.engine.chat_dispatch(self.task["id"])
        self.assertIsNotNone(d)
        self.assertEqual(d["role"], "local-model")

        report = {"schema_version": 1, "task_id": self.task["id"], "dispatch_id": d["id"],
                  "role": "local-model", "attempt": 1, "status": "DONE", "summary": "真实接口形状测试回复",
                  "basis": ["测试端点返回"], "suggestions": [], "next_action": "等待用户", "data": {}}
        class Response:
            def __enter__(self): return self
            def __exit__(self, *_): return False
            def read(self): return json.dumps({"choices": [{"message": {"content": json.dumps(report)}}],
                                               "usage": {"total_tokens": 9}}).encode()
        with patch("urllib.request.urlopen", return_value=Response()) as send:
            run = self.runtime.run(self.task["id"], d["id"], timeout=30)
        self.assertEqual(send.call_count, 1)
        self.assertEqual(run["status"], "COMPLETED")
        self.assertIsNotNone(run["usage"])

        # Check message delivered
        msgs = self.engine.messages(self.task["id"])
        self.assertEqual(len(msgs), 1)
        self.assertEqual(msgs[0]["status"], "DELIVERED")
        self.assertEqual(len(msgs[0]["replies"]), 1)
        self.assertEqual(msgs[0]["replies"][0]["role"], "local-model")
        self.assertIn("真实接口", msgs[0]["replies"][0]["content"])

    def test_unconfigured_remote_agent_never_fakes_a_reply(self):
        with patch.dict(os.environ, {"DEEPSEEK_API_KEY": ""}):
            with self.assertRaises(CollabError):
                self.engine.message_add(self.task["id"], "remote", "deepseek-hermes", "hello")


if __name__ == "__main__":
    unittest.main()
