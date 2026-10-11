"""Local file boundaries, deterministic API tool protocol, and no real models."""
import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from worktogether.core import Engine, CollabError, encode
from worktogether.agents import AgentRegistry
from worktogether.openai_runtime import OpenAICompatibleRuntime
from worktogether.read_tools import ReadTools, validate_tool_files, MAX_READ_BYTES, MAX_FILE_BYTES
from tests.core.test_group_chat import chat_report
from tests.core.test_server import DummyServer, execute_request
from worktogether.runtime import Runtime


def call(name="read_file", args=None, key="call-1"):
    return {"id": key, "type": "function", "function": {
        "name": name, "arguments": encode(args if args is not None else {"path": "src/main.txt"})}}


class Response:
    def __init__(self, message, usage=None):
        self.message, self.usage = message, usage or {"total_tokens": 3}

    def __enter__(self): return self
    def __exit__(self, *_): return False
    def read(self, *_):
        return encode({"choices": [{"message": self.message}], "usage": self.usage}).encode()


class ReadToolsTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name).resolve()
        (self.root / "src").mkdir()
        (self.root / "src/main.txt").write_text("hello world", encoding="utf-8")
        self.state = self.root / "state/state.sqlite3"

    def reader(self, files=None):
        return ReadTools(self.root, self.state, files or ["src/main.txt"])

    def test_grant_validation_rejects_unsafe_names_and_shapes(self):
        cases = [None, "src/main.txt", [True], ["a"] * 11, ["a", "a"], [""],
                 ["/tmp/a"], ["../a"], ["src/../a"], ["src//a"], ["src/./a"], ["src/"],
                 [".env"], ["src/.aws/keys"], ["src/secrets.json"], ["src/AUTH.json"],
                 ["src/cert.pem"], ["state.db"], ["a\\b"], ["a\x00b"], ["a:b"]]
        for files in cases:
            with self.subTest(files=files), self.assertRaises(CollabError):
                validate_tool_files(files)

    def test_lists_only_selected_files_and_reads_bounded_excerpt(self):
        (self.root / "unselected.txt").write_text("hidden")
        with self.reader() as tools:
            self.assertEqual(["src/main.txt"], tools.execute("list_files", "{}")['files'])
            result = tools.execute("read_file", encode({"path": "src/main.txt", "offset": 6, "limit": 3}))
            self.assertEqual("wor", result["content"])
            self.assertEqual(9, result["next_offset"])
            self.assertTrue(result["truncated"])
            self.assertEqual(64, len(result["excerpt_sha256"]))
            self.assertFalse(tools.execute("read_file", encode({"path": "unselected.txt"}))["ok"])

    def test_rejects_symlinks_in_any_component_and_hard_links(self):
        (self.root / "alias").symlink_to(self.root / "src", target_is_directory=True)
        (self.root / "linked.txt").symlink_to(self.root / "src/main.txt")
        os.link(self.root / "src/main.txt", self.root / "hard.txt")
        with self.reader(["alias/main.txt", "linked.txt", "hard.txt"]) as tools:
            for name in tools.files:
                self.assertEqual("TOOL_PATH_DENIED", tools.execute("read_file", encode({"path": name}))["error"])

    def test_rejects_fifo_directory_binary_and_oversized_files(self):
        os.mkfifo(self.root / "pipe")
        (self.root / "binary").write_bytes(b"x\0y")
        with (self.root / "large").open("wb") as out:
            out.truncate(MAX_FILE_BYTES + 1)
        with self.reader(["pipe", "src", "binary", "large"]) as tools:
            for name, error in [("pipe", "TOOL_PATH_DENIED"), ("src", "TOOL_PATH_DENIED"),
                                ("binary", "TOOL_BINARY_FILE"), ("large", "TOOL_FILE_TOO_LARGE")]:
                self.assertEqual(error, tools.execute("read_file", encode({"path": name}))["error"])

    def test_protects_state_directory_and_top_level_run_artifacts(self):
        (self.root / "state").mkdir()
        (self.root / "state/notes.txt").write_text("private")
        with self.reader(["state/notes.txt"]) as tools:
            self.assertEqual([], tools.execute("list_files", "{}")["files"])
            self.assertFalse(tools.execute("read_file", '{"path":"state/notes.txt"}')["ok"])
        (self.root / "runs").mkdir()
        (self.root / "runs/log.txt").write_text("private")
        with ReadTools(self.root, self.root / "db", ["db", "db-wal", "runs/log.txt", "execution-a.lock"]) as tools:
            for name in tools.files:
                self.assertFalse(tools.execute("read_file", encode({"path": name}))["ok"])

    def test_denied_tools_and_bad_arguments_consume_budget(self):
        with self.reader() as tools:
            for name, args in [("run_command", '{"command":"touch NO"}'), ("write_file", "{}"),
                               ("read_file", "[]"), ("list_files", '{"directory":"/"}')]:
                self.assertFalse(tools.execute(name, args)["ok"])
            with self.assertRaises(CollabError) as exc:
                tools.execute("list_files", "{}")
            self.assertEqual("TOOL_LIMIT", exc.exception.code)
        self.assertFalse((self.root / "NO").exists())

    def test_read_ranges_reject_booleans_negative_and_excessive_values(self):
        for arg in [{"offset": True}, {"offset": -1}, {"limit": 0}, {"limit": MAX_READ_BYTES + 1}]:
            with self.reader() as tools:
                self.assertEqual("TOOL_ARGUMENTS", tools.execute("read_file", encode({"path": "src/main.txt", **arg}))["error"])

    def test_file_swapped_to_symlink_just_before_open_is_rejected(self):
        original = os.open
        with self.reader() as tools:
            def swap_then_open(path, *args, **kwargs):
                if path == "main.txt":
                    (self.root / "src/main.txt").unlink()
                    (self.root / "src/main.txt").symlink_to("/etc/hosts")
                return original(path, *args, **kwargs)
            with patch("worktogether.read_tools.os.open", side_effect=swap_then_open):
                self.assertEqual("TOOL_PATH_DENIED", tools.execute("read_file", '{"path":"src/main.txt"}')["error"])

    def test_default_byte_limit_and_closed_descriptor(self):
        (self.root / "src/main.txt").write_text("a" * 50000)
        with self.reader() as tools:
            result = tools.execute("read_file", '{"path":"src/main.txt"}')
            self.assertEqual(MAX_READ_BYTES, result["bytes"])
            self.assertEqual(MAX_READ_BYTES, len(result["content"]))
            self.assertTrue(result["truncated"])
        self.assertIsNone(tools.root_fd)
        self.assertEqual("TOOLS_UNAVAILABLE", tools.execute("read_file", '{"path":"src/main.txt"}')["error"])


class ApiReadToolsTests(unittest.TestCase):
    def setUp(self):
        ReadToolsTests.setUp(self)
        self.engine = Engine(self.state)
        self.registry = AgentRegistry(self.engine)
        self.registry.register({"id": "api-reader", "name": "Fixture", "type": "openai_compat",
                                "endpoint": "http://127.0.0.1:1/v1", "model": "fixture"})
        project = self.engine.project_add("fixture", str(self.root))
        self.task = self.engine.task_add(project["id"], "Read", "Review selected files")["id"]
        self.runtime = OpenAICompatibleRuntime(self.engine, self.registry.get("api-reader"))

    def reserve(self, files=None, **kwargs):
        self.engine.message_add(self.task, "read", "api-reader", "review", tool_files=files, **kwargs)
        dispatch = self.engine.chat_dispatch(self.task)
        return self.runtime._reserve(self.task, dispatch["id"], 30)

    def test_tool_loop_sends_real_excerpt_and_aggregates_usage(self):
        run, dispatch = self.reserve(["src/main.txt"])
        answers = [Response({"tool_calls": [call("list_files", {})]}),
                   Response({"tool_calls": [call(key="read-file")]}), Response({"content": "Read hello world"})]
        with patch("worktogether.openai_runtime._open_no_redirect", side_effect=answers) as send:
            result = self.runtime.execute_reserved(run, dispatch)
        self.assertEqual("COMPLETED", result["status"])
        self.assertEqual(9, result["usage"]["total_tokens"])
        self.assertEqual(2, result["tool_calls"])
        last = json.loads(send.call_args.args[0].data)
        self.assertEqual("hello world", json.loads(last["messages"][-1]["content"])["content"])
        self.assertEqual({"read_file", "list_files"}, {t["function"]["name"] for t in last["tools"]})
        events = (Path(run["artifact_dir"]) / "events.jsonl").read_text()
        self.assertIn("excerpt_sha256", events)
        self.assertNotIn("hello world", events)
        self.assertEqual("hello world", (self.root / "src/main.txt").read_text())

    def test_default_mode_has_no_tools_and_rejects_unrequested_calls(self):
        run, dispatch = self.reserve()
        with patch("worktogether.openai_runtime._open_no_redirect", return_value=Response({"tool_calls": [call()]})) as send:
            result = self.runtime.execute_reserved(run, dispatch)
        self.assertNotIn("tools", json.loads(send.call_args.args[0].data))
        self.assertEqual("TOOL_NOT_ALLOWED", result["error_code"])
        self.assertEqual([], self.engine.messages(self.task)[0]["replies"])

    def test_pause_before_tool_execution_prevents_read_and_next_request(self):
        run, dispatch = self.reserve(["src/main.txt"])

        def pause(*_args, **_kwargs):
            self.engine.pause(self.task)
            return Response({"tool_calls": [call()]})

        with patch("worktogether.openai_runtime._open_no_redirect", side_effect=pause) as send, \
                patch.object(ReadTools, "execute") as read:
            result = self.runtime.execute_reserved(run, dispatch)
        read.assert_not_called()
        self.assertEqual(1, send.call_count)
        self.assertEqual("INVALID_STATE", result["error_code"])
        self.assertEqual({"total_tokens": 3}, result["usage"])

    def test_file_scope_revocation_prevents_next_http_request(self):
        run, dispatch = self.reserve(["src/main.txt"])
        # Revoke after the result has been recorded, before it is sent remotely.
        event = self.runtime._event
        def record_then_revoke(root, data):
            event(root, data)
            if data["event"] == "tool.result":
                with self.engine.transaction() as db:
                    db.execute("UPDATE messages SET body=json_set(body,'$.tool_files',json('[]')) WHERE task_id=?", (self.task,))

        with patch("worktogether.openai_runtime._open_no_redirect", return_value=Response({"tool_calls": [call()]})) as send, \
                patch.object(self.runtime, "_event", side_effect=record_then_revoke):
            result = self.runtime.execute_reserved(run, dispatch)
        self.assertEqual(1, send.call_count)
        self.assertEqual("PERMISSION_REVOKED", result["error_code"])

    def test_four_tool_calls_allow_only_one_final_request(self):
        run, dispatch = self.reserve(["src/main.txt"])
        answers = [Response({"tool_calls": [call(key=str(i))]}) for i in range(5)]
        with patch("worktogether.openai_runtime._open_no_redirect", side_effect=answers) as send:
            result = self.runtime.execute_reserved(run, dispatch)
        self.assertEqual(5, send.call_count)
        self.assertEqual("none", json.loads(send.call_args.args[0].data)["tool_choice"])
        self.assertEqual("TOOL_LIMIT", result["error_code"])
        self.assertEqual(4, result["tool_calls"])
        self.assertEqual(15, result["usage"]["total_tokens"])

    def test_duplicate_tool_ids_never_execute_twice(self):
        run, dispatch = self.reserve(["src/main.txt"])
        with patch("worktogether.openai_runtime._open_no_redirect", return_value=Response({"tool_calls": [call()]})) as send:
            result = self.runtime.execute_reserved(run, dispatch)
        self.assertEqual(2, send.call_count)
        self.assertEqual(1, result["tool_calls"])
        self.assertEqual("PROVIDER_FAILED", result["error_code"])

    def test_grant_is_not_inherited_by_peer_messages(self):
        self.engine.message_add(self.task, "root", "api-reader,codex", "review", tool_files=["src/main.txt"])
        dispatches = self.engine.chat_dispatch_round(self.task)
        for dispatch in dispatches:
            data = {"messages": [{"to": "api-reader", "intent": "ask", "content": "follow up"}]} if dispatch["role"] == "codex" else {}
            self.engine.submit(self.task, chat_report(dispatch, data=data))
        self.assertNotIn("tool_files", self.engine.messages(self.task)[-1])
        self.assertNotIn("tool_files", self.engine.chat_dispatch(self.task)["payload"])

    def test_http_accepts_only_valid_explicit_grant_and_never_upgrades_idempotent_message(self):
        server = DummyServer(self.engine, Runtime(self.engine), str(self.root))
        path = f"/api/tasks/{self.task}/messages"
        body = {"message_id": "http", "recipient": "api-reader", "content": "review"}
        self.assertEqual(400, execute_request(server, "POST", path, {**body, "tool_files": ["../escape"]})[0])
        self.assertEqual(202, execute_request(server, "POST", path, body)[0])
        response = execute_request(server, "POST", path, {**body, "tool_files": ["src/main.txt"]})[1]
        self.assertNotIn("tool_files", response)

    def test_tool_file_does_not_bypass_limits_through_attachments_or_readme(self):
        (self.root / "README.md").write_text("secret orientation")
        run, dispatch = self.reserve(["src/main.txt"], context_files=["src/main.txt"])
        self.assertEqual([], dispatch["payload"]["attached_files"])
        with patch("worktogether.openai_runtime._open_no_redirect", return_value=Response({"content": "No reads yet"})) as send:
            self.runtime.execute_reserved(run, dispatch)
        self.assertNotIn("secret orientation", send.call_args.args[0].data.decode())
        self.assertNotIn("hello world", send.call_args.args[0].data.decode())

    def test_endpoint_change_after_tool_result_blocks_forwarding_file_content(self):
        run, dispatch = self.reserve(["src/main.txt"])
        event = self.runtime._event
        def record_then_change(root, data):
            event(root, data)
            if data["event"] == "tool.result":
                self.registry.update_profile("api-reader", {"endpoint": "http://127.0.0.1:2/v1"})
        with patch("worktogether.openai_runtime._open_no_redirect", return_value=Response({"tool_calls": [call()]})) as send, \
                patch.object(self.runtime, "_event", side_effect=record_then_change):
            result = self.runtime.execute_reserved(run, dispatch)
        self.assertEqual(1, send.call_count)
        self.assertEqual("PERMISSION_REVOKED", result["error_code"])

    def test_unknown_tools_and_unselected_files_return_denials_without_execution(self):
        run, dispatch = self.reserve(["src/main.txt"])
        answers = [Response({"tool_calls": [call(args={"path": "../secrets"}),
                    call("run_command", {"command": "touch marker"}, key="shell")]}),
                   Response({"content": "Denied; no changes made"})]
        with patch("worktogether.openai_runtime._open_no_redirect", side_effect=answers) as send:
            result = self.runtime.execute_reserved(run, dispatch)
        self.assertEqual("COMPLETED", result["status"])
        payload = json.loads(send.call_args.args[0].data)
        outputs = [json.loads(m["content"]) for m in payload["messages"] if m["role"] == "tool"]
        self.assertEqual(["TOOL_PATH_DENIED", "TOOL_NOT_ALLOWED"], [o["error"] for o in outputs])
        self.assertFalse((self.root / "marker").exists())

    def test_loopback_http_lists_reads_and_reports_without_models(self):
        import errno
        import threading
        from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
        received = []
        answers = [{"tool_calls": [call("list_files", {})]},
                   {"tool_calls": [call(key="read")]}, {"content": "Read the authorized file"}]

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *_): pass
            def do_POST(self):
                received.append(json.loads(self.rfile.read(int(self.headers["Content-Length"]))))
                answer = encode({"choices": [{"message": answers[len(received) - 1]}],
                                 "usage": {"total_tokens": 2}}).encode()
                self.send_response(200)
                self.send_header("Content-Length", str(len(answer)))
                self.end_headers()
                self.wfile.write(answer)

        try:
            server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        except OSError as exc:
            if exc.errno in (errno.EPERM, errno.EACCES) and os.environ.get("WT_REQUIRE_LOOPBACK_TESTS") != "1":
                self.skipTest("Loopback binding is disabled in this sandbox")
            raise
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        self.addCleanup(thread.join, 2)
        self.addCleanup(server.server_close)
        self.addCleanup(server.shutdown)
        self.registry.update_profile("api-reader", {"endpoint": f"http://127.0.0.1:{server.server_port}/v1"})
        self.runtime = OpenAICompatibleRuntime(self.engine, self.registry.get("api-reader"))
        run, dispatch = self.reserve(["src/main.txt"])
        result = self.runtime.execute_reserved(run, dispatch)
        self.assertEqual("COMPLETED", result["status"])
        self.assertEqual(3, len(received))
        self.assertEqual("hello world", json.loads(received[-1]["messages"][-1]["content"])["content"])
        self.assertEqual(6, result["usage"]["total_tokens"])
        self.assertEqual("DELIVERED", self.engine.messages(self.task)[0]["status"])

    def test_malformed_batch_is_rejected_before_any_tool_executes(self):
        run, dispatch = self.reserve(["src/main.txt"])
        with patch("worktogether.openai_runtime._open_no_redirect", return_value=Response({"tool_calls": [call(), call()]})), \
                patch.object(ReadTools, "execute") as read:
            result = self.runtime.execute_reserved(run, dispatch)
        read.assert_not_called()
        self.assertEqual("PROVIDER_FAILED", result["error_code"])

    def test_one_time_budget_spans_all_tool_requests(self):
        run, dispatch = self.reserve(["src/main.txt"])
        event = self.runtime._event
        def record_then_expire(root, data):
            event(root, data)
            if data["event"] == "tool.result":
                run["created_at"] -= 100
        with patch("worktogether.openai_runtime._open_no_redirect", return_value=Response({"tool_calls": [call()]})) as send, \
                patch.object(self.runtime, "_event", side_effect=record_then_expire):
            result = self.runtime.execute_reserved(run, dispatch)
        self.assertEqual(1, send.call_count)
        self.assertEqual("RUN_TIMEOUT", result["error_code"])

    def test_incompatible_api_does_not_retry_or_silently_remove_tools(self):
        import urllib.error
        run, dispatch = self.reserve(["src/main.txt"])
        with patch("worktogether.openai_runtime._open_no_redirect", side_effect=urllib.error.HTTPError(
                "http://127.0.0.1:1/v1/chat/completions", 400, "Tools unsupported", {}, None)) as send:
            result = self.runtime.execute_reserved(run, dispatch)
        self.assertEqual(1, send.call_count)
        self.assertEqual("FAILED", result["status"])
        self.assertIn("tools", json.loads(send.call_args.args[0].data))
        self.assertEqual([], self.engine.chat_dispatch_round(self.task))
