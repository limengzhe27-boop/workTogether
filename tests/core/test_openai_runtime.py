"""Tests for OpenAICompatibleRuntime with DeepSeek Hermes and custom models."""
import json
import os
import tempfile
import unittest
import urllib.request
from unittest.mock import patch

from worktogether.core import Engine, CollabError
from worktogether.agents import AgentRegistry
from worktogether.openai_runtime import OpenAICompatibleRuntime, _NoRedirect, probe_openai_compatible


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
            def read(self, *_): return json.dumps({"choices": [{"message": {"content": json.dumps(report)}}],
                                               "usage": {"total_tokens": 9}}).encode()
        with patch("worktogether.openai_runtime._open_no_redirect", return_value=Response()) as send:
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

    def test_api_agent_executes_read_only_analysis(self):
        with self.engine.transaction() as db:
            task = self.engine._task(db, self.task["id"])
            task["phase"] = "EXECUTING"
            task["steps"] = [{"id": "s1", "title": "Analyze", "work_kind": "analysis",
                              "assignee": "local-model", "acceptance": ["Identify contract"],
                              "depends_on": [], "status": "PENDING", "version": None,
                              "repair_count": 0, "handoff": None, "verification": None}]
            self.engine._save(db, task, "TEST_PLAN", {})
        d = self.engine.dispatch(self.task["id"])["dispatch"]
        self.assertEqual(d["kind"], "ANALYZE")
        with self.assertRaises(CollabError) as approval_error:
            self.runtime.run(self.task["id"], d["id"], timeout=30)
        self.assertEqual(approval_error.exception.code, "APPROVAL_REQUIRED")
        self.runtime.approval(self.task["id"], self.runtime.approval(self.task["id"])["digest"])
        report = {"schema_version": 1, "task_id": self.task["id"], "dispatch_id": d["id"],
                  "role": "local-model", "attempt": 1, "status": "DONE", "summary": "API v2",
                  "basis": ["Local fixture"], "suggestions": [], "next_action": "Review",
                  "data": {"deliverable": "Use v2", "evidence": ["Observed contract"]}}
        class Response:
            def __enter__(self): return self
            def __exit__(self, *_): return False
            def read(self, *_): return json.dumps({"choices": [{"message": {"content": json.dumps(report)}}]}).encode()
        with patch("worktogether.openai_runtime._open_no_redirect", return_value=Response()) as send:
            run = self.runtime.run(self.task["id"], d["id"], timeout=30)
        self.assertEqual(run["status"], "COMPLETED")
        sent = send.call_args.args[0]
        self.assertNotIn("Authorization", sent.headers)
        self.assertEqual(self.engine.show(self.task["id"])["steps"][0]["status"], "VERIFYING")

    def test_http_redirects_are_rejected(self):
        import urllib.request
        request = urllib.request.Request("https://api.example.test/v1/chat/completions",
                                         headers={"Authorization": "Bearer secret"})
        handler = _NoRedirect()
        self.assertIsNone(handler.redirect_request(
            request, None, 302, "Found", {}, "https://other.example.test/steal"))

    def test_explicit_probe_sends_no_task_content_and_reports_usable_response(self):
        class Response:
            def __enter__(self): return self
            def __exit__(self, *_): return False
            def read(self, *_):
                return b'{"choices":[{"message":{"content":"PONG"}}],"usage":{"total_tokens":8}}'
        with patch("worktogether.openai_runtime._open_no_redirect", return_value=Response()) as send:
            result = probe_openai_compatible(self.agent_cfg)
        self.assertTrue(result["connected"])
        self.assertEqual(8, result["usage"]["total_tokens"])
        request = send.call_args.args[0]
        self.assertEqual(10, send.call_args.kwargs["timeout"])
        payload = json.loads(request.data)
        self.assertEqual(16, payload["max_tokens"])
        self.assertNotIn("Req", request.data.decode())
        self.assertNotIn(self.temp_dir.name, request.data.decode())

    def test_probe_missing_key_does_not_contact_remote(self):
        cfg = self.registry.get("deepseek-hermes")
        with patch.dict(os.environ, {"DEEPSEEK_API_KEY": ""}), \
             patch("worktogether.openai_runtime._open_no_redirect") as send:
            result = probe_openai_compatible(cfg)
        self.assertEqual("MISSING_KEY", result["code"])
        send.assert_not_called()

    def test_probe_rejects_non_chat_response(self):
        class Response:
            def __enter__(self): return self
            def __exit__(self, *_): return False
            def read(self, *_): return b'{"choices":[]}'
        with patch("worktogether.openai_runtime._open_no_redirect", return_value=Response()):
            result = probe_openai_compatible(self.agent_cfg)
        self.assertEqual("INCOMPATIBLE_RESPONSE", result["code"])

    def test_malformed_provider_response_fails_without_delivering(self):
        self.engine.message_add(self.task["id"], "malformed", "local-model", "hello")
        d = self.engine.chat_dispatch(self.task["id"])
        class Response:
            def __enter__(self): return self
            def __exit__(self, *_): return False
            def read(self, *_): return b'{"choices":[{"message":{"content":null}}]}'
        with patch("worktogether.openai_runtime._open_no_redirect", return_value=Response()):
            run = self.runtime.run(self.task["id"], d["id"], timeout=30)
        self.assertEqual(run["status"], "FAILED")
        self.assertEqual(self.engine.messages(self.task["id"])[0]["status"], "BLOCKED")

    def test_pause_after_reservation_prevents_api_request(self):
        self.engine.message_add(self.task["id"], "pause", "local-model", "hello")
        d = self.engine.chat_dispatch(self.task["id"])
        run, reserved = self.runtime._reserve(self.task["id"], d["id"], 30)
        self.engine.pause(self.task["id"])
        with patch("worktogether.openai_runtime._open_no_redirect") as send:
            result = self.runtime.execute_reserved(run, reserved)
        send.assert_not_called()
        self.assertEqual(result["status"], "FAILED")

    def reserve_chat(self, recipient="local-model"):
        self.engine.message_add(self.task["id"], "guard", recipient, "review only")
        dispatch = self.engine.chat_dispatch(self.task["id"])
        return self.runtime._reserve(self.task["id"], dispatch["id"], 30)

    def assert_request_blocked(self, run, dispatch, code):
        with patch("worktogether.openai_runtime._open_no_redirect") as send:
            result = self.runtime.execute_reserved(run, dispatch)
        send.assert_not_called()
        self.assertEqual("FAILED", result["status"])
        self.assertEqual(code, result["error_code"])
        message = self.engine.messages(self.task["id"])[0]
        self.assertEqual("BLOCKED", message["status"])
        self.assertEqual([], message["replies"])
        self.assertEqual("BLOCKED", self.engine.show(self.task["id"])["dispatches"][-1]["status"])

    def test_prepared_api_rechecks_enabled_member(self):
        run, dispatch = self.reserve_chat()
        # Simulate an external importer; normal registry removal already rejects
        # active members. Launch must still validate the live state, like a CLI.
        with self.engine.transaction() as db:
            db.execute("UPDATE agents SET body=json_set(body,'$.enabled',json('false')) WHERE id='local-model'")
        self.assert_request_blocked(run, dispatch, "ROOM_SCOPE_CHANGED")

    def test_prepared_api_rechecks_original_room_scope(self):
        run, dispatch = self.reserve_chat()
        with self.engine.transaction() as db:
            db.execute("UPDATE messages SET body=json_set(body,'$.room_members',json('[]')) WHERE task_id=?",
                       (self.task["id"],))
        self.assert_request_blocked(run, dispatch, "ROOM_SCOPE_CHANGED")
        # Failure stays visible; the queue must not quietly try the call again.
        self.assertEqual([], self.engine.chat_dispatch_round(self.task["id"]))
        self.assertEqual(1, len(self.runtime.list(self.task["id"])["runs"]))
        self.engine.message_resolve(self.task["id"], "guard", "skip")
        self.assertEqual("DELIVERED", self.engine.messages(self.task["id"])[0]["status"])

    def test_prepared_api_rechecks_current_team(self):
        run, dispatch = self.reserve_chat()
        with self.engine.transaction() as db:
            task = self.engine._task(db, self.task["id"])
            task["team"] = {"members": ["codex", "antigravity"],
                            "coordinator": "codex", "reviewer": "antigravity"}
            self.engine._save(db, task, "TEST_IMPORT", {})
        self.assert_request_blocked(run, dispatch, "ROOM_SCOPE_CHANGED")

    def test_clearing_stored_key_blocks_prepared_api_with_cached_credentials(self):
        self.registry.register({"id": "remote-model", "name": "Fixture", "type": "openai_compat",
                                "endpoint": "https://example.invalid/v1", "model": "fixture",
                                "secret_key": "not-a-real-key"})
        self.runtime = OpenAICompatibleRuntime(self.engine, self.registry.get("remote-model"))
        run, dispatch = self.reserve_chat("remote-model")
        self.registry.set_secret("remote-model", "")
        self.assert_request_blocked(run, dispatch, "ROOM_SCOPE_CHANGED")

    def test_pause_during_request_preparation_prevents_api_send(self):
        run, dispatch = self.reserve_chat()
        request_class = urllib.request.Request

        def prepare_then_pause(*args, **kwargs):
            request = request_class(*args, **kwargs)
            self.engine.pause(self.task["id"])
            return request

        with patch("worktogether.openai_runtime.urllib.request.Request", side_effect=prepare_then_pause):
            self.assert_request_blocked(run, dispatch, "INVALID_STATE")
        self.assertEqual("PAUSED", self.engine.show(self.task["id"])["status"])

    def test_api_refuses_an_imported_write_grant_even_when_authorized(self):
        self.engine.chat_config(self.task["id"], {"allow_write": True})
        run, dispatch = self.reserve_chat()
        self.assertFalse(dispatch["payload"].get("allow_write"))
        with self.engine.transaction() as db:
            task = self.engine._task(db, self.task["id"])
            current = self.engine._get_dispatch(task, dispatch["id"])
            current["payload"]["allow_write"] = True
            self.engine._save(db, task, "TEST_IMPORT", {})
        self.assert_request_blocked(run, dispatch, "UNSUPPORTED_PROVIDER")

    def test_api_request_in_flight_can_finish_while_task_is_paused(self):
        run, dispatch = self.reserve_chat()
        engine, task_id = self.engine, self.task["id"]

        class Response:
            def __enter__(self): return self
            def __exit__(self, *_): return False
            def read(self, *_):
                # No database lock is held over the network wait. A user pause
                # can succeed; it doesn't pretend to cancel an in-flight HTTP call.
                engine.pause(task_id)
                return json.dumps({"choices": [{"message": {"content": "Fixture reply"}}]}).encode()

        with patch("worktogether.openai_runtime._open_no_redirect", return_value=Response()) as send:
            result = self.runtime.execute_reserved(run, dispatch)
        self.assertEqual(1, send.call_count)
        self.assertEqual("COMPLETED", result["status"])
        self.assertEqual("PAUSED", engine.show(task_id)["status"])
        self.assertEqual([], engine.chat_dispatch_round(task_id))
        self.assertFalse(json.loads(send.call_args.args[0].data).get("tools"))


if __name__ == "__main__":
    unittest.main()
