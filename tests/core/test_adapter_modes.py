"""Which adapters may change files, and what argv that produces."""
import json
import tempfile
import unittest
from pathlib import Path

from worktogether.agents import AgentConfig
from worktogether.antigravity import AntigravityRuntime
from worktogether.core import Engine
from worktogether.harness import HarnessRuntime


def dispatch(kind, allow_write=None):
    payload = {"step": {}, "draft_plan": None, "requirement_revision": 1}
    if allow_write is not None:
        payload["allow_write"] = allow_write
    return {"kind": kind, "role": "antigravity", "task_id": "t", "id": "d",
            "attempt": 1, "payload": payload, "version": None}


class AdapterWriteModeTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.engine = Engine(str(Path(self.tmp.name) / "state.sqlite3"))

    def run_dict(self, timeout=30):
        return {"timeout": timeout, "session_id": None, "cwd": self.tmp.name,
                "artifact_dir": str(Path(self.tmp.name) / "artifacts")}

    # ---- Antigravity ------------------------------------------------------------
    def test_antigravity_edits_only_when_the_turn_may_write(self):
        runtime = AntigravityRuntime(self.engine, "agy")
        run = self.run_dict()
        writing = runtime.command(run, dispatch("CHAT", allow_write=True))
        self.assertIn("accept-edits", writing)
        self.assertIn("--dangerously-skip-permissions", writing)
        # The write turn keeps the sandbox and names its workspace, so the
        # boundary matches the other adapters instead of the whole machine.
        self.assertIn("--sandbox", writing)
        self.assertIn(run["cwd"], writing)

        reading = runtime.command(self.run_dict(), dispatch("CHAT", allow_write=False))
        self.assertIn("plan", reading)
        self.assertIn("--sandbox", reading)
        self.assertNotIn("--dangerously-skip-permissions", reading)

    def test_antigravity_work_dispatches_always_write(self):
        runtime = AntigravityRuntime(self.engine, "agy")
        for kind in ("CODE", "FIX"):
            with self.subTest(kind=kind):
                args = runtime.command(self.run_dict(), dispatch(kind))
                self.assertIn("accept-edits", args)

    def test_antigravity_read_only_chat_is_told_so(self):
        runtime = AntigravityRuntime(self.engine, "agy")
        prompt = runtime._prompt(dispatch("CHAT", allow_write=False))
        self.assertIn("read-only plan mode", prompt)
        writable_prompt = runtime._prompt(dispatch("CHAT", allow_write=True))
        self.assertNotIn("read-only plan mode", writable_prompt)

    # ---- DeepSeek Harness -------------------------------------------------------
    def policy_for(self, kind, allow_write):
        runtime = HarnessRuntime(self.engine, "dsh", role="deepseek-harness")
        run = self.run_dict()
        Path(run["artifact_dir"]).mkdir(parents=True, exist_ok=True)
        runtime.command(run, dispatch(kind, allow_write))
        return json.loads((Path(run["artifact_dir"]) / "harness-policy.json").read_text())

    def test_harness_chat_write_mode_follows_the_grant(self):
        self.assertEqual("workspace-write", self.policy_for("CHAT", True)[0]["config"]["mode"])
        self.assertEqual("read-only", self.policy_for("CHAT", False)[0]["config"]["mode"])
        self.assertEqual("read-only", self.policy_for("CHAT", None)[0]["config"]["mode"])

    def test_harness_work_dispatches_always_write(self):
        for kind in ("CODE", "FIX"):
            with self.subTest(kind=kind):
                self.assertEqual("workspace-write", self.policy_for(kind, None)[0]["config"]["mode"])

    def test_harness_never_resumes_a_read_session_for_a_write_turn(self):
        runtime = HarnessRuntime(self.engine, "dsh", role="deepseek-harness")
        read = runtime.session_scope(dispatch("CHAT", allow_write=False))
        write = runtime.session_scope(dispatch("CHAT", allow_write=True))
        self.assertNotEqual(read, write)
        self.assertEqual(runtime.session_scope(dispatch("CODE")), write)

    # ---- agent configuration ----------------------------------------------------
    def test_writable_tracks_the_adapter_not_the_brand(self):
        cases = [
            ({"id": "codex", "name": "Codex", "type": "cli"}, True),
            ({"id": "antigravity", "name": "AGY", "type": "cli"}, True),
            ({"id": "deepseek-harness", "name": "Harness", "type": "cli"}, True),
            ({"id": "cloned", "name": "Cloned", "type": "cli", "base_agent": "antigravity"}, True),
            ({"id": "api", "name": "API", "type": "openai_compat", "endpoint": "http://127.0.0.1:9/v1",
              "model": "m"}, False),
            ({"id": "codex", "name": "Codex", "type": "cli", "enabled": False}, False),
        ]
        for data, expected in cases:
            with self.subTest(agent=data["id"], expected=expected):
                self.assertEqual(expected, AgentConfig(data).writable)

    def test_registry_reports_writability(self):
        from worktogether.agents import AgentRegistry
        from unittest.mock import patch
        registry = AgentRegistry(self.engine)
        agents = {a["id"]: a for a in registry.list()}
        self.assertTrue(agents["codex"]["writable"])
        self.assertTrue(agents["antigravity"]["writable"])
        self.assertFalse(agents["deepseek-hermes"]["writable"])
        # Harness ships disabled; writability follows enablement like readiness.
        self.assertFalse(agents["deepseek-harness"]["writable"])
        with patch("worktogether.agents.shutil.which", return_value="/usr/local/bin/dsh"):
            registry.set_enabled("deepseek-harness", True)
        self.assertTrue({a["id"]: a for a in registry.list()}["deepseek-harness"]["writable"])


if __name__ == "__main__":
    unittest.main()
