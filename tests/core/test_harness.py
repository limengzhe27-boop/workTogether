import json
import os
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from types import SimpleNamespace

from worktogether.agents import AgentRegistry
from worktogether.core import Engine
from worktogether.harness import HarnessRuntime, MAC_EXECUTABLE
from worktogether.runtime import Runtime
from worktogether.antigravity import AntigravityRuntime
from worktogether.teamwork import discuss_team
from worktogether.execution import execute


class HarnessTests(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.engine = Engine(Path(tmp.name) / "state.db")
        self.registry = AgentRegistry(self.engine)
        self.fixture = str(Path(__file__).parent / "fake_harness.py")
        project = self.engine.project_add("test", tmp.name)
        self.task = self.engine.task_add(project["id"], "task", "Build a feature")["id"]
        self.h = HarnessRuntime(self.engine, self.fixture)

    def enable(self):
        with patch("worktogether.agents.shutil.which", return_value=self.fixture):
            self.registry.set_enabled("deepseek-harness", True)

    def chat(self, message="m"):
        self.enable()
        self.engine.message_add(self.task, message, "deepseek-harness", "Discuss only")
        dispatch = self.engine.chat_dispatch(self.task)
        return self.h.run(self.task, dispatch["id"], 10)

    def test_opt_in_resume_and_strict_policy(self):
        self.assertFalse(self.registry.get("deepseek-harness").enabled)
        first = self.chat()
        self.assertEqual("COMPLETED", first["status"])
        second = self.chat("second")
        self.assertEqual(first["session_id"], second["session_id"])
        self.assertEqual({"input_tokens": 7, "output_tokens": 3}, second["usage"])
        root = Path(second["artifact_dir"])
        command = json.loads((root / "command.json").read_text())
        self.assertIn("--session-id", command)
        policy = json.loads((root / "harness-policy.json").read_text())
        # A chat turn in a writable session may change files; the write grant is
        # what decides, not the dispatch kind alone.
        self.assertEqual("workspace-write", policy[0]["config"]["mode"])
        self.assertEqual("never", policy[1]["config"]["policy"])
        self.assertIn({"id": "tool-subagent", "disabled": True}, policy)
        self.assertNotEqual(self.h.session_scope({"kind": "CODE"}), self.h.session_scope({"kind": "VERIFY"}))

    def test_harness_chat_is_read_only_when_the_session_forbids_writes(self):
        self.enable()
        self.engine.chat_config(self.task, {"allow_write": False})
        self.engine.message_add(self.task, "readonly", "deepseek-harness", "Discuss only")
        dispatch = self.engine.chat_dispatch(self.task)
        self.assertFalse(dispatch["payload"].get("allow_write"))
        run = self.h.run(self.task, dispatch["id"], 10)
        policy = json.loads((Path(run["artifact_dir"]) / "harness-policy.json").read_text())
        self.assertEqual("read-only", policy[0]["config"]["mode"])
        # A read session must never be resumed for a write turn.
        self.assertNotEqual(self.h.session_scope(dispatch),
                            self.h.session_scope({"kind": "CODE", "payload": {}}))

    def test_three_agent_discussion_and_non_codex_code(self):
        self.enable()
        self.engine.configure_team(self.task, ["codex", "antigravity", "deepseek-harness"], "deepseek-harness", "codex")
        root = Path(__file__).parent
        codex = Runtime(self.engine, str(root / "fake_codex.py"))
        agy = AntigravityRuntime(self.engine, str(root / "fake_agy.py"))
        with patch.object(AgentRegistry, "get_runtime", return_value=self.h):
            result = discuss_team(self.engine, self.task, codex, agy)
            self.assertEqual("PLANNING_FINISHED", result["reason"])
            self.assertEqual(9, len(result["runs"]))
            approval = codex.approval(self.task)
            codex.approval(self.task, approval["digest"])
            result = execute(self.engine, self.task, codex, agy)
        self.assertEqual("COMPLETED", result["reason"])
        step = self.engine.show(self.task)["steps"][0]
        self.assertEqual("deepseek-harness", step["assignee"])
        self.assertEqual("codex", step["verifier"])
        sessions = {run["session_scope"]: run["session_id"] for run in codex.list(self.task)["runs"]
                    if run["provider"] == "deepseek_harness"}
        self.assertNotEqual(sessions["deepseek_harness:read"], sessions["deepseek_harness:write"])

    def test_bad_streams_never_pass(self):
        for mode in ("wrong-cwd", "error", "missing-final", "duplicate"):
            with self.subTest(mode=mode):
                # Each failure blocks its task; use a fresh task for the next case.
                project = self.engine.show(self.task)["project_id"]
                self.task = self.engine.task_add(project, "bad", "Bad stream")["id"]
                with patch.dict(os.environ, {"WT_HARNESS_FIXTURE": mode}):
                    result = self.chat(mode)
                self.assertEqual("FAILED", result["status"])
                self.assertEqual("BLOCKED", self.engine.show(self.task)["status"])

    def test_missing_usage_is_unknown_not_zero(self):
        with patch.dict(os.environ, {"WT_HARNESS_FIXTURE": "missing-usage"}):
            result = self.chat()
        self.assertEqual("COMPLETED", result["status"])
        self.assertIsNone(result["usage"])

    def test_cli_collect_dispatches_to_harness_after_restart(self):
        from worktogether.__main__ import run as cli_run
        result = self.chat()
        self.assertEqual("COMPLETED", result["status"])
        with self.engine.transaction() as db:
            saved = self.h._get(db, result["id"])
            saved["status"] = "EXITED"
            self.h._save(db, saved)
        collected = cli_run(SimpleNamespace(db=self.engine.path, command="collect", run=result["id"]))
        self.assertEqual("COMPLETED", collected["status"])

    def test_registry_cannot_disable_a_team_member_mid_task(self):
        self.enable()
        self.engine.configure_team(self.task, ["codex", "deepseek-harness"])
        from worktogether.core import CollabError
        with self.assertRaises(CollabError):
            self.registry.set_enabled("deepseek-harness", False)

    @unittest.skipUnless(Path(MAC_EXECUTABLE).is_file(), "Local Harness application not installed")
    def test_installed_policy_composes_without_model_call(self):
        with tempfile.TemporaryDirectory() as directory:
            adapter = HarnessRuntime(self.engine, MAC_EXECUTABLE)
            command = adapter.command({"cwd": directory, "artifact_dir": directory, "session_id": None}, {"kind": "CONSULT"})
            result = subprocess.run(command[:-2] + ["--dump-config"],
                                    env=dict(os.environ, DSH_HOME=directory + "/isolated-home"),
                                    capture_output=True, text=True, timeout=20)
            self.assertEqual(0, result.returncode, result.stderr)
            self.assertIn("read-only", result.stdout)
            self.assertIn("sandbox-policy", result.stdout)
