"""Tests for agent cloning, editable profiles and local secret storage."""
import json
import os
import stat
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from worktogether.agents import AgentRegistry
from worktogether.core import CollabError, Engine


class AgentProfileTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.engine = Engine(str(Path(self.tmp.name) / "state.sqlite3"))
        self.registry = AgentRegistry(self.engine)
        self.secrets_path = Path(self.tmp.name) / "secrets.json"

    def test_cloned_cli_agent_inherits_base_capabilities(self):
        created = self.registry.register({
            "id": "codex-helper", "name": "Codex 助手", "type": "cli",
            "base_agent": "codex", "system_prompt": "Answer in Chinese.",
        })
        self.assertEqual("cli", created["type"])
        self.assertEqual("codex", created["base_agent"])
        self.assertEqual("codex", created["executable"])
        for role in ("code", "plan", "chat", "verify"):
            self.assertIn(role, created["roles"])
        self.assertEqual("Answer in Chinese.", created["system_prompt"])

    def test_cloned_agent_rejects_unknown_base(self):
        with self.assertRaises(CollabError):
            self.registry.register({"id": "weird", "name": "Weird", "type": "cli", "base_agent": "claude"})

    def test_builtin_agents_cannot_be_replaced_or_removed(self):
        for agent_id in ("codex", "antigravity", "deepseek-harness"):
            with self.subTest(agent_id=agent_id):
                with self.assertRaises(CollabError):
                    self.registry.remove(agent_id)
                with self.assertRaises(CollabError):
                    self.registry.register({"id": agent_id, "name": "fake", "type": "openai_compat",
                                            "endpoint": "http://127.0.0.1:9/v1", "model": "m"})

    def test_secret_is_kept_out_of_the_registry_database(self):
        with patch.dict(os.environ, {}, clear=False):
            created = self.registry.register({
                "id": "local-api", "name": "Local API", "type": "openai_compat",
                "endpoint": "http://127.0.0.1:9/v1", "model": "m", "secret_key": "sk-local-1",
            })
        self.assertTrue(created["has_stored_key"])
        self.assertTrue(created["has_key"])
        self.assertTrue(self.secrets_path.is_file())
        self.assertEqual("sk-local-1", json.loads(self.secrets_path.read_text(encoding="utf-8"))["local-api"])
        with self.engine.transaction() as db:
            body = db.execute("SELECT body FROM agents WHERE id='local-api'").fetchone()[0]
        self.assertNotIn("sk-local-1", body)

    def test_secret_file_is_owner_only(self):
        self.registry.register({"id": "local-api", "name": "Local API", "type": "openai_compat",
                                "endpoint": "http://127.0.0.1:9/v1", "model": "m", "secret_key": "sk"})
        mode = stat.S_IMODE(self.secrets_path.stat().st_mode)
        self.assertEqual(0o600, mode)

    def test_environment_variable_takes_precedence_over_stored_secret(self):
        self.registry.register({"id": "local-api", "name": "Local API", "type": "openai_compat",
                                "endpoint": "http://127.0.0.1:9/v1", "model": "m",
                                "api_key_env": "DEEPSEEK_API_KEY", "secret_key": "sk-stored"})
        with patch.dict(os.environ, {"DEEPSEEK_API_KEY": "sk-env"}):
            self.assertEqual("sk-env", self.registry.get("local-api").resolve_api_key())
        with patch.dict(os.environ, {}, clear=True):
            self.assertEqual("sk-stored", self.registry.get("local-api").resolve_api_key())

    def test_profile_update_changes_persona_and_clears_secret(self):
        self.registry.register({"id": "local-api", "name": "Local API", "type": "openai_compat",
                                "endpoint": "http://127.0.0.1:9/v1", "model": "m", "secret_key": "sk"})
        updated = self.registry.update_profile("local-api", {
            "name": "Renamed", "description": "desc", "system_prompt": "Be terse.", "model": "m2"})
        self.assertEqual("Renamed", updated["name"])
        self.assertEqual("Be terse.", updated["system_prompt"])
        self.assertEqual("m2", updated["model"])
        cleared = self.registry.update_profile("local-api", {"secret_key": ""})
        self.assertFalse(cleared["has_stored_key"])
        self.assertEqual({}, json.loads(self.secrets_path.read_text(encoding="utf-8")))

    def test_profile_update_rejects_oversized_fields(self):
        self.registry.register({"id": "local-api", "name": "Local API", "type": "openai_compat",
                                "endpoint": "http://127.0.0.1:9/v1", "model": "m"})
        with self.assertRaises(CollabError):
            self.registry.update_profile("local-api", {"system_prompt": "x" * 4001})

    def test_removing_an_agent_drops_its_secret(self):
        self.registry.register({"id": "local-api", "name": "Local API", "type": "openai_compat",
                                "endpoint": "http://127.0.0.1:9/v1", "model": "m", "secret_key": "sk"})
        self.assertTrue(self.registry.remove("local-api"))
        self.assertEqual({}, json.loads(self.secrets_path.read_text(encoding="utf-8")))

    def test_disabled_or_env_backed_agents_report_readiness_honestly(self):
        self.registry.register({"id": "local-api", "name": "Local API", "type": "openai_compat",
                                "endpoint": "http://127.0.0.1:9/v1", "model": "m"})
        self.registry.set_enabled("local-api", False)
        self.assertFalse(self.registry.get("local-api").ready)
        self.assertFalse(self.registry.get("local-api").has_key)


if __name__ == "__main__":
    unittest.main()
