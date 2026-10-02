"""Unit tests for WorkTogether HTTP server and REST API without raw socket network binding.

Tests the HTTP handler logic, routing, JSON validation, security guards, and error responses
using in-memory simulated HTTP streams (compatible with strict sandbox environments).
"""
import io
import json
import os
import re
import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

from worktogether.adapters import MockAdapter
from worktogether.core import Engine
from worktogether.runtime import Runtime
from worktogether.server import WorkTogetherHandler, resolve_antigravity_executable


class DummyServer:
    def __init__(self, engine, runtime, static_dir):
        self.engine = engine
        self.runtime = runtime
        self.static_dir = static_dir


class SimulatedSocket:
    def __init__(self, request_bytes):
        self._rfile = io.BytesIO(request_bytes)
        self._wfile = io.BytesIO()

    def makefile(self, mode="r", *args, **kwargs):
        if "b" in mode:
            if "r" in mode:
                return self._rfile
            else:
                return self._wfile
        if "r" in mode:
            return io.TextIOWrapper(self._rfile, encoding="utf-8")
        return io.TextIOWrapper(self._wfile, encoding="utf-8")

    def sendall(self, data):
        self._wfile.write(data)

    def close(self):
        pass


def execute_request(server, method, path, body=None, headers=None):
    headers = headers or {}
    body_bytes = b""
    if body is not None:
        if isinstance(body, (dict, list)):
            body_bytes = json.dumps(body).encode("utf-8")
            headers["Content-Type"] = "application/json"
        elif isinstance(body, str):
            body_bytes = body.encode("utf-8")
        elif isinstance(body, bytes):
            body_bytes = body
        headers["Content-Length"] = str(len(body_bytes))

    req_lines = [f"{method} {path} HTTP/1.1", "Host: 127.0.0.1"]
    for k, v in headers.items():
        req_lines.append(f"{k}: {v}")
    req_lines.append("\r\n")
    raw_req = "\r\n".join(req_lines).encode("utf-8") + body_bytes

    sock = SimulatedSocket(raw_req)

    # Instantiate handler with simulated socket and dummy server
    class TestHandler(WorkTogetherHandler):
        def __init__(self, request, client_address, srv):
            self.server = srv
            self.connection = request
            self.rfile = request._rfile
            self.wfile = request._wfile
            self.client_address = client_address
            # parse and handle
            self.handle_one_request()

    TestHandler(sock, ("127.0.0.1", 12345), server)

    # Parse response from sock._wfile
    raw_resp = sock._wfile.getvalue()
    parts = raw_resp.split(b"\r\n\r\n", 1)
    header_part = parts[0].decode("utf-8", errors="replace")
    body_part = parts[1] if len(parts) > 1 else b""

    status_line = header_part.split("\r\n")[0]
    status_code = int(status_line.split(" ")[1])

    try:
        parsed_body = json.loads(body_part.decode("utf-8"))
    except Exception:
        parsed_body = body_part.decode("utf-8", errors="replace")

    return status_code, parsed_body


class ServerApiTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.db_path = os.path.join(self.temp_dir.name, "web_test.db")
        self.static_dir = os.path.join(self.temp_dir.name, "static")
        os.makedirs(self.static_dir, exist_ok=True)
        with open(os.path.join(self.static_dir, "index.html"), "w", encoding="utf-8") as f:
            f.write("<!DOCTYPE html><html><body>WorkTogether Web Workspace</body></html>")

        self.engine = Engine(self.db_path)
        self.runtime = Runtime(self.engine, executable="codex")
        self.server = DummyServer(self.engine, self.runtime, self.static_dir)

    def tearDown(self):
        self.temp_dir.cleanup()

    def test_health_and_static_serving(self):
        status, data = execute_request(self.server, "GET", "/api/health")
        self.assertEqual(status, 200)
        self.assertEqual(data["status"], "ok")

        self.server.provider_status = {"antigravity": {"available": False, "executable": "agy"}}
        status, data = execute_request(self.server, "GET", "/api/health")
        self.assertEqual(status, 200)
        self.assertFalse(data["providers"]["antigravity"]["available"])

        status, html = execute_request(self.server, "GET", "/")
        self.assertEqual(status, 200)
        self.assertIn("WorkTogether Web Workspace", html)

    def test_private_antigravity_cli_is_used_only_when_executable(self):
        candidate = Path(self.temp_dir.name) / "antigravity"
        candidate.write_text("fixture", encoding="utf-8")
        with patch("worktogether.server.shutil.which", return_value=None):
            self.assertEqual(resolve_antigravity_executable("agy", candidate), "agy")
            candidate.chmod(0o700)
            self.assertEqual(resolve_antigravity_executable("agy", candidate), str(candidate))
            self.assertEqual(resolve_antigravity_executable("/explicit/agy", candidate), "/explicit/agy")

    def test_real_static_assets_packaging(self):
        real_static_dir = Path(__file__).resolve().parent.parent.parent / "worktogether" / "static"
        real_server = DummyServer(self.engine, self.runtime, str(real_static_dir))
        
        status, html = execute_request(real_server, "GET", "/")
        self.assertEqual(status, 200)
        self.assertIn('<div id="root"></div>', html)
        js_path = re.search(r'src="([^"]+\.js)"', html).group(1)
        css_path = re.search(r'href="([^"]+\.css)"', html).group(1)
        self.assertTrue(js_path.startswith('/assets/'))
        status, css = execute_request(real_server, "GET", css_path)
        self.assertEqual(status, 200)
        self.assertIn(":root", css)

        status, js = execute_request(real_server, "GET", js_path)
        self.assertEqual(status, 200)
        self.assertIn("WorkTogether", js)

    def test_projects_and_tasks_crud_and_isolation(self):
        dir_a = os.path.join(self.temp_dir.name, "proj_a")
        os.makedirs(dir_a, exist_ok=True)
        status, proj_a = execute_request(self.server, "POST", "/api/projects", {"name": "Project A", "path": dir_a})
        self.assertEqual(status, 201)
        self.assertIn("id", proj_a)

        dir_b = os.path.join(self.temp_dir.name, "proj_b")
        os.makedirs(dir_b, exist_ok=True)
        status, proj_b = execute_request(self.server, "POST", "/api/projects", {"name": "Project B", "path": dir_b})
        self.assertEqual(status, 201)

        status, res = execute_request(self.server, "GET", "/api/projects")
        self.assertEqual(status, 200)
        self.assertEqual(len(res["projects"]), 2)

        # Add task A1
        status, task_a = execute_request(self.server, "POST", "/api/tasks", {
            "project_id": proj_a["id"],
            "title": "Task A1",
            "requirement": "Requirement for A1"
        })
        self.assertEqual(status, 201)

        # Add task B1
        status, task_b = execute_request(self.server, "POST", "/api/tasks", {
            "project_id": proj_b["id"],
            "title": "Task B1",
            "requirement": "Requirement for B1"
        })
        self.assertEqual(status, 201)

        # Verify isolation: Project A tasks only contains Task A1
        status, res_a = execute_request(self.server, "GET", f"/api/projects/{proj_a['id']}/tasks")
        self.assertEqual(status, 200)
        self.assertEqual(len(res_a["tasks"]), 1)
        self.assertEqual(res_a["tasks"][0]["id"], task_a["id"])

        status, res_b = execute_request(self.server, "GET", f"/api/projects/{proj_b['id']}/tasks")
        self.assertEqual(status, 200)
        self.assertEqual(len(res_b["tasks"]), 1)
        self.assertEqual(res_b["tasks"][0]["id"], task_b["id"])

    def test_dispatch_submit_lifecycle_via_http(self):
        dir_p = os.path.join(self.temp_dir.name, "proj")
        os.makedirs(dir_p, exist_ok=True)
        _, proj = execute_request(self.server, "POST", "/api/projects", {"name": "Test Proj", "path": dir_p})
        _, task = execute_request(self.server, "POST", "/api/tasks", {
            "project_id": proj["id"], "title": "Lifecycle Task", "requirement": "Full test"
        })
        task_id = task["id"]

        # 1. Dispatch PLAN
        status, d_res = execute_request(self.server, "POST", f"/api/tasks/{task_id}/dispatch")
        self.assertEqual(status, 200)
        d_plan = d_res["dispatch"]
        self.assertEqual(d_plan["kind"], "PLAN")

        # 2. Get template
        status, tpl_res = execute_request(self.server, "GET", f"/api/tasks/{task_id}/template/{d_plan['id']}")
        self.assertEqual(status, 200)
        self.assertIn("template", tpl_res)

        # 3. Get handoff bundle
        status, handoff_res = execute_request(self.server, "GET", f"/api/tasks/{task_id}/handoff/{d_plan['id']}")
        self.assertEqual(status, 200)
        self.assertEqual(handoff_res["dispatch"]["id"], d_plan["id"])

        # 4. Submit PLAN
        report = MockAdapter().prepare(d_plan, tpl_res["template"])
        status, sub_res = execute_request(self.server, "POST", f"/api/tasks/{task_id}/submit", report)
        self.assertEqual(status, 200)
        self.assertTrue(sub_res["accepted"])

        # 5. Check task detail
        status, t_show = execute_request(self.server, "GET", f"/api/tasks/{task_id}")
        self.assertEqual(status, 200)
        self.assertEqual(t_show["phase"], "PLAN_REVIEW")

        # 6. Pause and resume
        status, pause_res = execute_request(self.server, "POST", f"/api/tasks/{task_id}/pause")
        self.assertEqual(status, 200)
        self.assertEqual(pause_res["status"], "PAUSED")

        status, d_paused = execute_request(self.server, "POST", f"/api/tasks/{task_id}/dispatch")
        self.assertIsNone(d_paused["dispatch"])

        status, resume_res = execute_request(self.server, "POST", f"/api/tasks/{task_id}/resume")
        self.assertEqual(status, 200)
        self.assertEqual(resume_res["status"], "ACTIVE")

    def test_manual_verification_recovery_via_http(self):
        _, project = execute_request(self.server, "POST", "/api/projects", {
            "name": "Manual Recovery", "path": self.temp_dir.name})
        _, task = execute_request(self.server, "POST", "/api/tasks", {
            "project_id": project["id"], "title": "Recover", "requirement": "Verify one step"})
        task_id = task["id"]
        adapter = MockAdapter()
        for _ in range(3):
            status, result = execute_request(self.server, "POST", f"/api/tasks/{task_id}/dispatch")
            self.assertEqual(status, 200)
            dispatch = result["dispatch"]
            status, template = execute_request(self.server, "GET", f"/api/tasks/{task_id}/template/{dispatch['id']}")
            self.assertEqual(status, 200)
            report = adapter.prepare(dispatch, template["template"])
            self.assertEqual(execute_request(self.server, "POST", f"/api/tasks/{task_id}/submit", report)[0], 200)

        status, result = execute_request(self.server, "POST", f"/api/tasks/{task_id}/dispatch")
        self.assertEqual(status, 200)
        code = result["dispatch"]
        _, template = execute_request(self.server, "GET", f"/api/tasks/{task_id}/template/{code['id']}")
        self.assertEqual(execute_request(self.server, "POST", f"/api/tasks/{task_id}/submit",
                                         adapter.prepare(code, template["template"]))[0], 200)

        _, result = execute_request(self.server, "POST", f"/api/tasks/{task_id}/dispatch")
        verify = result["dispatch"]
        self.assertEqual(verify["kind"], "VERIFY")
        blocked = self.engine.template(task_id, verify["id"])
        blocked["summary"] = "Verification tool permission denied"
        blocked["basis"] = ["Permission prompt could not be accepted"]
        blocked["data"] = {"reason": "Permission denied"}
        self.assertEqual(execute_request(self.server, "POST", f"/api/tasks/{task_id}/submit", blocked)[0], 200)
        self.assertEqual(self.engine.show(task_id)["status"], "BLOCKED")

        status, retried = execute_request(self.server, "POST", f"/api/tasks/{task_id}/retry",
                                          {"dispatch_id": verify["id"]})
        self.assertEqual(status, 200)
        replacement = retried["dispatch"]
        self.assertNotEqual(replacement["id"], verify["id"])
        self.assertEqual(replacement["kind"], "VERIFY")
        status, bundle = execute_request(self.server, "GET", f"/api/tasks/{task_id}/handoff/{replacement['id']}")
        self.assertEqual(status, 200)
        self.assertEqual(bundle["template"]["attempt"], replacement["attempt"])
        passed = adapter.prepare(replacement, bundle["template"])
        self.assertEqual(execute_request(self.server, "POST", f"/api/tasks/{task_id}/submit", passed)[0], 200)
        self.assertEqual(self.engine.show(task_id)["status"], "COMPLETED")

    def test_demo_endpoint(self):
        dir_demo = os.path.join(self.temp_dir.name, "demo_ws")
        os.makedirs(dir_demo, exist_ok=True)
        status, res = execute_request(self.server, "POST", "/api/demo", {"name": "Demo WS", "path": dir_demo})
        self.assertEqual(status, 201)
        self.assertEqual(res["status"], "SIMULATED_COMPLETED")
        self.assertEqual(res["task"]["status"], "COMPLETED")

    def test_security_guards(self):
        # 1. Invalid route -> 404
        status, res = execute_request(self.server, "GET", "/api/invalid_route_xyz")
        self.assertEqual(status, 404)

        # 2. Path traversal rejection in artifact endpoint (returns 403 or 404)
        status, res = execute_request(self.server, "GET", "/api/runs/r1/artifacts/../../etc/passwd")
        self.assertIn(status, (403, 404))

        # 3. Disallowed artifact filename (safe whitelist rejects with 403)
        status, res = execute_request(self.server, "GET", "/api/runs/r1/artifacts/malicious.sh")
        self.assertEqual(status, 403)

    def test_agents_api(self):
        # 1. GET /api/agents returns default agents
        status, res = execute_request(self.server, "GET", "/api/agents")
        self.assertEqual(status, 200)
        self.assertIn("agents", res)
        agent_ids = [a["id"] for a in res["agents"]]
        self.assertIn("codex", agent_ids)
        self.assertIn("antigravity", agent_ids)
        self.assertIn("deepseek-hermes", agent_ids)

        # 2. POST /api/agents creates new custom agent
        new_agent = {
            "id": "qwen-coder",
            "name": "Qwen Coder",
            "type": "openai_compat",
            "endpoint": "http://localhost:11434/v1",
            "model": "qwen2.5-coder:32b",
            "roles": ["code", "chat"],
            "color": "#8b5cf6",
            "avatar": "🚀"
        }
        status, res = execute_request(self.server, "POST", "/api/agents", new_agent)
        self.assertEqual(status, 201)
        self.assertEqual(res["id"], "qwen-coder")

        # 3. Verify it appears in GET /api/agents
        status, res = execute_request(self.server, "GET", "/api/agents")
        self.assertEqual(status, 200)
        agent_ids = [a["id"] for a in res["agents"]]
        self.assertIn("qwen-coder", agent_ids)


if __name__ == "__main__":
    unittest.main()
