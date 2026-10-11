"""HTTP-level tests for project context, admin and agent profile routes."""
import json
import os
import subprocess
import tempfile
import unittest
from pathlib import Path

from worktogether.core import Engine
from worktogether.runtime import Runtime
from tests.core.test_server import DummyServer, execute_request


class ServerContextTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp_dir.cleanup)
        self.project_dir = Path(self.temp_dir.name) / "project"
        self.project_dir.mkdir()
        subprocess.run(["git", "init", "-q", str(self.project_dir)], check=True)
        (self.project_dir / "README.md").write_text("# Fixture\n", encoding="utf-8")
        (self.project_dir / "tracked.txt").write_text("original\n", encoding="utf-8")
        subprocess.run(["git", "add", "-A"], cwd=self.project_dir, check=True)
        subprocess.run(["git", "-c", "user.email=t@t", "-c", "user.name=t", "commit", "-qm", "init"],
                       cwd=self.project_dir, check=True)

        self.engine = Engine(os.path.join(self.temp_dir.name, "state.sqlite3"))
        self.server = DummyServer(self.engine, Runtime(self.engine, executable="codex"),
                                  os.path.join(self.temp_dir.name, "static"))
        self.project = self.engine.project_add("fixture", str(self.project_dir))
        self.project_id = self.project["id"]
        self.task_id = self.engine.task_add(self.project_id, "task", "requirement")["id"]

    def test_files_route_returns_paths_with_sizes(self):
        status, data = execute_request(self.server, "GET", f"/api/projects/{self.project_id}/files")
        self.assertEqual(200, status)
        self.assertEqual(self.project_id, data["project_id"])
        entries = {entry["path"]: entry["size"] for entry in data["files"]}
        self.assertIn("README.md", entries)
        self.assertGreater(entries["README.md"], 0)
        self.assertEqual(len(data["files"]), data["total"])
        self.assertFalse(data["truncated"])

    def test_memory_route_round_trips_and_appends(self):
        status, data = execute_request(self.server, "GET", f"/api/projects/{self.project_id}/memory")
        self.assertEqual((200, ""), (status, data["memory"]))
        status, data = execute_request(self.server, "POST", f"/api/projects/{self.project_id}/memory",
                                       {"memory": "line one"})
        self.assertEqual((200, "line one"), (status, data["memory"]))
        status, data = execute_request(self.server, "POST", f"/api/projects/{self.project_id}/memory",
                                       {"memory": "line two", "append": True})
        self.assertEqual("line one\nline two", data["memory"])
        status, data = execute_request(self.server, "POST", f"/api/projects/{self.project_id}/memory", {})
        self.assertEqual(400, status)
        self.assertEqual("INVALID_INPUT", data["error"]["code"])

    def test_memory_route_rejects_unknown_project(self):
        status, data = execute_request(self.server, "GET", "/api/projects/deadbeef/memory")
        self.assertEqual(404, status)
        self.assertEqual("NOT_FOUND", data["error"]["code"])

    def test_diff_route_reports_modified_files(self):
        (self.project_dir / "tracked.txt").write_text("changed\n", encoding="utf-8")
        status, data = execute_request(self.server, "GET", f"/api/projects/{self.project_id}/diff")
        self.assertEqual(200, status)
        self.assertEqual(self.project_id, data["project_id"])
        self.assertTrue(data["is_git"])
        self.assertEqual("tracked.txt", data["files"][0]["path"])

    def test_revert_route_confirms_what_changed(self):
        (self.project_dir / "tracked.txt").write_text("changed\n", encoding="utf-8")
        status, data = execute_request(self.server, "POST", f"/api/projects/{self.project_id}/revert-file",
                                       {"path": "tracked.txt"})
        self.assertEqual(200, status)
        self.assertTrue(data["reverted"])
        self.assertEqual("tracked.txt", data["path"])
        self.assertEqual("checkout", data["mode"])
        self.assertEqual("original\n", (self.project_dir / "tracked.txt").read_text(encoding="utf-8"))

    def test_revert_route_blocks_path_escape(self):
        status, data = execute_request(self.server, "POST", f"/api/projects/{self.project_id}/revert-file",
                                       {"path": "../../etc/hosts"})
        self.assertEqual(400, status)
        self.assertEqual("FORBIDDEN", data["error"]["code"])

    def test_agent_delete_route_returns_stable_shape(self):
        self.register_local_api_agent()
        status, data = execute_request(self.server, "POST", "/api/agents/local-api/delete", {})
        self.assertEqual(200, status)
        self.assertTrue(data["deleted"])
        self.assertEqual("local-api", data["agent_id"])

    def register_local_api_agent(self):
        from worktogether.agents import AgentRegistry
        AgentRegistry(self.engine).register({
            "id": "local-api", "name": "Local API", "type": "openai_compat",
            "endpoint": "http://127.0.0.1:9/v1", "model": "m", "secret_key": "sk-test",
        })

    def test_agent_profile_route_updates_persona_without_echoing_secrets(self):
        self.register_local_api_agent()
        status, data = execute_request(self.server, "POST", "/api/agents/local-api/profile",
                                       {"system_prompt": "Be precise.", "secret_key": "sk-new"})
        self.assertEqual(200, status)
        self.assertEqual("Be precise.", data["system_prompt"])
        self.assertTrue(data["has_stored_key"])
        self.assertNotIn("sk-new", json.dumps(data))

    def test_task_delete_route_returns_stable_shape(self):
        status, data = execute_request(self.server, "POST", f"/api/tasks/{self.task_id}/delete", {})
        self.assertEqual(200, status)
        self.assertEqual({"deleted": True, "task_id": self.task_id}, data)

    def test_task_rename_route_updates_title(self):
        status, data = execute_request(self.server, "POST", f"/api/tasks/{self.task_id}/rename",
                                       {"title": "renamed"})
        self.assertEqual(200, status)
        self.assertEqual("renamed", data["title"])

    def test_project_delete_route_reports_cascade(self):
        status, data = execute_request(self.server, "POST", f"/api/projects/{self.project_id}/delete", {})
        self.assertEqual(200, status)
        self.assertEqual({"deleted": True, "project_id": self.project_id, "tasks_removed": 1}, data)
        self.assertEqual([], self.engine.projects_list())

    def test_file_preview_route_reads_bounded_content(self):
        status, data = execute_request(self.server, "GET", f"/api/projects/{self.project_id}/file?path=README.md")
        self.assertEqual(200, status)
        self.assertEqual(self.project_id, data["project_id"])
        self.assertEqual("README.md", data["path"])
        self.assertIn("# Fixture", data["content"])
        self.assertFalse(data["truncated"])

    def test_file_preview_route_truncates_and_rejects_escapes(self):
        (self.project_dir / "big.txt").write_text("y" * 20000, encoding="utf-8")
        status, data = execute_request(self.server, "GET", f"/api/projects/{self.project_id}/file?path=big.txt")
        self.assertEqual(200, status)
        self.assertEqual(12000, len(data["content"]))
        self.assertTrue(data["truncated"])
        self.assertEqual(20000, data["size_bytes"])

        for path in ("../../etc/hosts", "missing.txt", ".collab/state.sqlite3"):
            with self.subTest(path=path):
                status, data = execute_request(
                    self.server, "GET", f"/api/projects/{self.project_id}/file?path={path}")
                self.assertEqual(404, status)
                self.assertEqual("NOT_FOUND", data["error"]["code"])

    def test_health_reports_current_feature_set(self):
        status, data = execute_request(self.server, "GET", "/api/health")
        self.assertEqual(200, status)
        self.assertIn("project-context", data["features"])
        self.assertIn("project-memory", data["features"])

    def test_unknown_routes_still_404(self):
        status, data = execute_request(self.server, "GET", "/api/nope")
        self.assertEqual(404, status)
        self.assertEqual("NOT_FOUND", data["error"]["code"])


if __name__ == "__main__":
    unittest.main()
