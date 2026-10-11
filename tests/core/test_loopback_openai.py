"""Actual loopback HTTP transport test; the server is a deterministic fixture."""
import errno
import json
import os
import tempfile
import threading
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from worktogether.adapters import MockAdapter
from worktogether.agents import AgentRegistry
from worktogether.antigravity import AntigravityRuntime
from worktogether.chat import ChatQueue
from worktogether.core import Engine
from worktogether.execution import execute
from worktogether.openai_runtime import probe_openai_compatible
from worktogether.runtime import Runtime


class LoopbackOpenAITransportTests(unittest.TestCase):
    def test_probe_discussion_and_delegated_analysis_over_real_http(self):
        received = []

        class FixtureHandler(BaseHTTPRequestHandler):
            def log_message(self, *_):
                pass

            def do_POST(self):
                if self.path != "/v1/chat/completions":
                    self.send_error(404)
                    return
                length = int(self.headers["Content-Length"])
                payload = json.loads(self.rfile.read(length))
                received.append({"payload": payload, "authorization": self.headers.get("Authorization")})
                content = payload["messages"][-1]["content"]
                if content == "Connection check only. Reply with PONG.":
                    answer = "PONG"
                else:
                    dispatch = json.loads(content.split("DISPATCH:\n", 1)[1])
                    assert dispatch["kind"] in ("CHAT", "ANALYZE")
                    report = {"schema_version": 1, "task_id": dispatch["task_id"],
                              "dispatch_id": dispatch["id"], "role": dispatch["role"],
                              "attempt": dispatch["attempt"], "status": "DONE",
                              "summary": "Read " + str(len(dispatch["payload"].get("peer_replies", []))) + " peer replies",
                              "basis": ["Fixture observation"], "suggestions": [],
                              "next_action": "Await user", "data": {}}
                    if dispatch["kind"] == "ANALYZE":
                        report["summary"] = "Analysis delivered"
                        report["data"] = {"deliverable": "Fixture interface map",
                                          "evidence": ["Local fixture response"]}
                    answer = json.dumps(report)
                body = json.dumps({"choices": [{"message": {"content": answer}}],
                                   "usage": {"total_tokens": 8}}).encode()
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

        try:
            server = ThreadingHTTPServer(("127.0.0.1", 0), FixtureHandler)
        except OSError as exc:
            if exc.errno in (errno.EPERM, errno.EACCES) and os.environ.get("WT_REQUIRE_LOOPBACK_TESTS") != "1":
                self.skipTest("Loopback binding is disabled in this sandbox")
            raise
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        self.addCleanup(thread.join, 2)
        self.addCleanup(server.server_close)
        self.addCleanup(server.shutdown)

        with tempfile.TemporaryDirectory() as temp:
            engine = Engine(Path(temp) / "state.sqlite3")
            registry = AgentRegistry(engine)
            registry.register({"id": "local-model", "name": "Local Model", "type": "openai_compat",
                               "endpoint": f"http://127.0.0.1:{server.server_port}/v1", "model": "fixture"})
            probe = probe_openai_compatible(registry.get("local-model"))
            self.assertTrue(probe["connected"], probe)
            self.assertEqual(8, probe["usage"]["total_tokens"])

            project = engine.project_add("fixture", temp)
            task = engine.task_add(project["id"], "discussion", "Compare approaches")
            message = engine.message_add(task["id"], "message-1", "all", "What should we do?")
            self.assertEqual(["codex", "antigravity", "local-model"], message["participants"])
            fixtures = Path(__file__).parent
            codex = Runtime(engine, str((fixtures / "fake_codex.py").resolve()))
            antigravity = AntigravityRuntime(engine, str((fixtures / "fake_agy.py").resolve()))
            queue = ChatQueue(engine, codex, antigravity)
            # One pass runs the whole round: all three agents answer at once.
            queue.once()
            delivered = engine.messages(task["id"])[0]
            self.assertEqual("DELIVERED", delivered["status"])
            self.assertEqual({"codex", "antigravity", "local-model"},
                             {reply["role"] for reply in delivered["replies"]})
            # Round 1 is a simultaneous broadcast, so nobody saw a peer reply.
            self.assertTrue(all(reply["round"] == 1 for reply in delivered["replies"]))
            self.assertEqual("Read 0 peer replies",
                             next(r["content"] for r in delivered["replies"] if r["role"] == "local-model"))
            self.assertEqual(2, len(received))
            self.assertEqual("fixture", received[-1]["payload"]["model"])
            self.assertIsNone(received[-1]["authorization"])

            adapter = MockAdapter()
            for _ in range(3):
                dispatch = engine.dispatch(task["id"])["dispatch"]
                report = adapter.prepare(dispatch, engine.template(task["id"], dispatch["id"]))
                if dispatch["kind"] == "PLAN_FINALIZE":
                    report["data"] = {"steps": [
                        {"id": "research", "title": "Map interface", "work_kind": "analysis",
                         "assignee": "local-model", "acceptance": ["Map interface"], "depends_on": []},
                        {"id": "implement", "title": "Use analysis", "work_kind": "code",
                         "assignee": "codex", "acceptance": ["Use analysis"],
                         "depends_on": ["research"]},
                    ]}
                engine.submit(task["id"], report)
            codex.approval(task["id"], codex.approval(task["id"])["digest"])
            outcome = execute(engine, task["id"], codex, antigravity, max_calls=6)
            self.assertEqual("COMPLETED", outcome["reason"], outcome)
            self.assertEqual(["ANALYZE", "VERIFY", "CODE", "VERIFY"],
                             [dispatch["kind"] for dispatch in engine.show(task["id"])["dispatches"][-4:]])
            self.assertEqual(3, len(received))
            self.assertEqual("Analysis delivered", engine.show(task["id"])["steps"][0]["handoff"]["summary"])


if __name__ == "__main__":
    unittest.main()
