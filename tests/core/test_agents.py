"""Tests for AgentRegistry and AgentConfig."""
import os
import json
import tempfile
import unittest
from unittest.mock import patch

from worktogether.core import Engine, CollabError
from worktogether.agents import AgentRegistry, AgentConfig


class AgentRegistryTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.db_path = os.path.join(self.temp_dir.name, "test_agents.db")
        self.engine = Engine(self.db_path)
        self.registry = AgentRegistry(self.engine)

    def tearDown(self):
        self.temp_dir.cleanup()

    def test_default_agents_seeded(self):
        agents = self.registry.list()
        ids = [a["id"] for a in agents]
        self.assertIn("codex", ids)
        self.assertIn("antigravity", ids)
        self.assertIn("deepseek-hermes", ids)

    def test_get_agent(self):
        ag = self.registry.get("deepseek-hermes")
        self.assertIsNotNone(ag)
        self.assertEqual(ag.name, "DeepSeek API（云端接口）")
        self.assertEqual(ag.type, "openai_compat")
        self.assertIn("chat", ag.roles)

    def test_legacy_deepseek_label_migration_preserves_configuration(self):
        with self.engine.transaction() as db:
            data = json.loads(db.execute("SELECT body FROM agents WHERE id='deepseek-hermes'").fetchone()[0])
            data.update(name="DeepSeek Hermes", enabled=False, api_key_env="MY_DEEPSEEK_KEY")
            db.execute("UPDATE agents SET name=?,body=? WHERE id=?",
                       (data["name"], json.dumps(data), data["id"]))
        AgentRegistry(self.engine)
        AgentRegistry(self.engine)  # Idempotent startup migration.
        current = self.registry.get("deepseek-hermes")
        self.assertEqual("DeepSeek API（云端接口）", current.name)
        self.assertFalse(current.enabled)
        self.assertEqual("MY_DEEPSEEK_KEY", current.api_key_env)
        self.assertEqual(data["endpoint"], current.endpoint)
        harness = self.registry.get("deepseek-harness")
        self.assertEqual("DeepSeek Harness（本机 CLI）", harness.name)
        self.assertFalse(harness.enabled)

    def test_custom_deepseek_name_or_endpoint_is_not_overwritten(self):
        for name, endpoint in (("My researcher", "https://api.deepseek.com/v1"),
                               ("DeepSeek Hermes", "http://127.0.0.1:1234/v1")):
            with self.subTest(name=name):
                self.registry.register(dict(id="deepseek-hermes", name=name, type="openai_compat",
                                            endpoint=endpoint, model="deepseek-chat"))
                AgentRegistry(self.engine)
                self.assertEqual(name, self.registry.get("deepseek-hermes").name)

    def test_register_custom_agent(self):
        custom = {
            "id": "claude-sonnet",
            "name": "Claude Sonnet",
            "type": "openai_compat",
            "endpoint": "http://127.0.0.1:11434/v1",
            "model": "claude-3-7-sonnet",
            "color": "#d97706",
            "avatar": "🎭",
            "description": "架构设计与安全审计专家",
        }
        res = self.registry.register(custom)
        self.assertEqual(res["id"], "claude-sonnet")

        fetched = self.registry.get("claude-sonnet")
        self.assertIsNotNone(fetched)
        self.assertEqual(fetched.name, "Claude Sonnet")
        self.assertEqual(fetched.roles, ["chat", "analysis", "plan", "review"])

    def test_remove_custom_agent(self):
        custom = {"id": "temp-agent", "name": "Temp", "type": "openai_compat",
                  "endpoint": "http://127.0.0.1:11434/v1", "model": "demo"}
        self.registry.register(custom)
        self.assertIsNotNone(self.registry.get("temp-agent"))

        ok = self.registry.remove("temp-agent")
        self.assertTrue(ok)
        self.assertIsNone(self.registry.get("temp-agent"))

    def test_removed_default_stays_removed_until_explicit_registration(self):
        original = self.registry.get("deepseek-hermes").to_dict()
        self.assertTrue(self.registry.remove("deepseek-hermes"))
        restarted = AgentRegistry(Engine(self.db_path))
        self.assertIsNone(restarted.get("deepseek-hermes"))
        self.assertIsNotNone(restarted.get("deepseek-harness"))
        self.assertFalse(restarted.get("deepseek-harness").enabled)
        self.assertFalse(restarted.remove("deepseek-hermes"))
        restarted.register(original)
        self.assertIsNotNone(AgentRegistry(self.engine).get("deepseek-hermes"))

    def queued_cloud_message(self, key="direct", recipient="deepseek-hermes"):
        project = self.engine.project_add("fixture", self.temp_dir.name)
        task = self.engine.task_add(project["id"], "Task", "Requirement")["id"]
        with patch.dict(os.environ, {"DEEPSEEK_API_KEY": "fixture-not-a-real-key"}):
            self.engine.message_add(task, key, recipient, "Unsent question")
        return task

    def test_explicit_removal_cancels_unsent_message_but_keeps_history(self):
        task = self.queued_cloud_message()
        with self.assertRaises(CollabError):
            self.registry.remove("deepseek-hermes")
        self.assertEqual("QUEUED", self.engine.messages(task)[0]["status"])
        self.assertTrue(self.registry.remove("deepseek-hermes", cancel_queued=True))
        message = self.engine.messages(task)[0]
        self.assertEqual("CANCELLED", message["status"])
        self.assertEqual("Unsent question", message["content"])
        self.assertIsNone(self.engine.chat_dispatch(task))
        self.assertEqual([], self.engine.show(task)["dispatches"])
        self.assertEqual(1, len([e for e in self.engine.show(task)["events"]
                                 if e["kind"] == "MESSAGE_CANCELLED"]))
        self.assertIsNone(AgentRegistry(self.engine).get("deepseek-hermes"))

    def test_remove_shared_conversation_rolls_back_other_cancellations(self):
        direct = self.queued_cloud_message()
        shared = self.queued_cloud_message("shared", "all")
        with self.assertRaises(CollabError):
            self.registry.remove("deepseek-hermes", cancel_queued=True)
        self.assertEqual("QUEUED", self.engine.messages(direct)[0]["status"])
        self.assertEqual("QUEUED", self.engine.messages(shared)[0]["status"])
        self.assertIsNotNone(AgentRegistry(self.engine).get("deepseek-hermes"))
        self.assertFalse(any(e["kind"] == "MESSAGE_CANCELLED" for e in self.engine.show(direct)["events"]))

    def test_remove_never_cancels_running_message(self):
        task = self.queued_cloud_message()
        with patch.dict(os.environ, {"DEEPSEEK_API_KEY": "fixture-not-a-real-key"}):
            self.engine.chat_dispatch(task)
            self.engine.message_add(task, "later", "deepseek-hermes", "Later question")
        with self.assertRaises(CollabError):
            self.registry.remove("deepseek-hermes", cancel_queued=True)
        self.assertEqual(["RUNNING", "QUEUED"], [m["status"] for m in self.engine.messages(task)])
        self.assertIsNotNone(self.registry.get("deepseek-hermes"))

    def test_built_in_protected(self):
        with self.assertRaises(CollabError):
            self.registry.remove("codex")
        with self.assertRaises(CollabError):
            self.registry.remove("deepseek-harness", cancel_queued=True)
        cfg = self.registry.get("codex")
        self.assertIsNotNone(cfg)
        self.assertTrue(cfg.enabled)

    def test_reject_secret_and_unsupported_cli(self):
        base = {"id": "custom", "name": "Custom", "type": "openai_compat",
                "endpoint": "https://api.example.com/v1", "model": "model"}
        with self.assertRaises(CollabError):
            self.registry.register(dict(base, api_key="secret"))
        with self.assertRaises(CollabError):
            self.registry.register(dict(base, type="cli"))
        with self.assertRaises(CollabError):
            self.registry.register(dict(base, endpoint="http://example.com/v1"))

    def test_legacy_plaintext_key_removed_from_live_row(self):
        with self.engine.transaction() as db:
            import json
            data = {"id": "old", "name": "Old", "type": "openai_compat",
                    "endpoint": "https://example.com/v1", "model": "test", "api_key": "old-secret"}
            db.execute("INSERT INTO agents VALUES(?,?,?,?)", ("old", "Old", "openai_compat", json.dumps(data)))
        AgentRegistry(self.engine)
        with self.engine.transaction() as db:
            body = db.execute("SELECT body FROM agents WHERE id='old'").fetchone()[0]
        self.assertNotIn("old-secret", body)


if __name__ == "__main__":
    unittest.main()
