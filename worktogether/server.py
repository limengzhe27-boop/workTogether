"""HTTP Server and REST API for WorkTogether Web Workspace.

Standard library only: http.server.ThreadingHTTPServer, json, urllib, sqlite3.
Implements DESIGN.md API contracts with security guards, background run execution,
and static web asset delivery.
"""
import json
import mimetypes
import os
import re
import shutil
import sys
import threading
import time
import urllib.parse
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from .adapters import MockAdapter
from .core import CollabError, Engine
from .runtime import Runtime
from .antigravity import AntigravityRuntime
from .jobs import PlanningJobs
from .chat import ChatQueue


SAFE_ARTIFACT_NAMES = {"prompt.txt", "command.json", "events.jsonl", "stderr.log", "result.json"}
ARTIFACT_PREVIEW_LIMIT = 512 * 1024


def resolve_antigravity_executable(executable, private_cli=None):
    if executable != "agy" or shutil.which("agy") is not None:
        return executable
    candidate = Path(private_cli) if private_cli else (
        Path(__file__).resolve().parent.parent / ".collab/tools/antigravity-1.2.8/antigravity")
    return str(candidate) if candidate.is_file() and os.access(candidate, os.X_OK) else executable


# The ChatGPT desktop app ships the Codex CLI without putting it on PATH. Codex
# is part of a ChatGPT subscription, so a user who has the app has Codex; look
# there before telling them it is missing. An explicit path always wins.
CODEX_APP_CANDIDATES = (
    "/Applications/ChatGPT.app/Contents/Resources/codex-cli/bin/codex",
    "/Applications/ChatGPT.app/Contents/Resources/codex-cli/CodexCLI.app/Contents/MacOS/codex",
)


def resolve_codex_executable(executable="codex", candidates=CODEX_APP_CANDIDATES):
    if executable != "codex" or shutil.which("codex") is not None:
        return executable
    for candidate in candidates:
        path = Path(candidate).expanduser()
        if path.is_file() and os.access(path, os.X_OK):
            return str(path)
    return executable


class WorkTogetherHandler(BaseHTTPRequestHandler):
    server_version = "WorkTogetherServer/1.1"

    @property
    def engine(self):
        return self.server.engine

    @property
    def runtime(self):
        return self.server.runtime

    @property
    def static_dir(self):
        return self.server.static_dir

    def log_message(self, format, *args):
        # Concise logging; suppress verbose request lines during test runs
        if os.environ.get("WORKTOGETHER_LOG_HTTP"):
            sys.stderr.write(f"[HTTP] {self.address_string()} - {format % args}\n")

    def _cors_headers(self):
        self.send_header("X-Content-Type-Options", "nosniff")

    def _local_request(self):
        host = self.headers.get("Host", "")
        parsed = urllib.parse.urlparse("http://" + host)
        if parsed.hostname not in ("127.0.0.1", "localhost", "::1"):
            self._send_error(403, "FORBIDDEN", "Local host required")
            return False
        origin = self.headers.get("Origin")
        if origin and origin != "http://" + host:
            self._send_error(403, "FORBIDDEN", "Cross-origin requests are prohibited")
            return False
        if self.headers.get("Sec-Fetch-Site") == "cross-site":
            self._send_error(403, "FORBIDDEN", "Cross-site requests are prohibited")
            return False
        return True

    def do_OPTIONS(self):
        if not self._local_request():
            return
        self.send_response(HTTPStatus.NO_CONTENT)
        self._cors_headers()
        self.end_headers()

    def _send_json(self, status_code, payload):
        body = json.dumps(payload, ensure_ascii=False, indent=2).encode("utf-8")
        self.send_response(status_code)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self._cors_headers()
        self.end_headers()
        self.wfile.write(body)

    def _send_error(self, status_code, code, message):
        self._send_json(status_code, {"error": {"code": code, "message": message}})

    def _read_json(self):
        try:
            length = int(self.headers.get("Content-Length", 0))
        except ValueError:
            raise CollabError("INVALID_INPUT", "Invalid content length")
        if length < 0:
            raise CollabError("INVALID_INPUT", "Invalid content length")
        if length > 5 * 1024 * 1024:
            raise CollabError("PAYLOAD_TOO_LARGE", "Request body exceeds 5MB limit")
        if length == 0:
            return {}
        raw = self.rfile.read(length).decode("utf-8")
        try:
            value = json.loads(raw)
            if not isinstance(value, dict):
                raise CollabError("INVALID_INPUT", "JSON object required")
            return value
        except json.JSONDecodeError as err:
            raise CollabError("INVALID_JSON", f"Malformed JSON: {err}")

    def do_GET(self):
        if not self._local_request():
            return
        parsed = urllib.parse.urlparse(self.path)
        path = parsed.path.rstrip("/")
        query = urllib.parse.parse_qs(parsed.query)

        try:
            # 1. Health check
            if path == "/api/health":
                return self._send_json(HTTPStatus.OK, {"status": "ok", "version": "v1.2",
                                                       "features": ["task-teams", "bounded-team-discussion",
                                                                    "delegate-team", "deepseek-harness",
                                                                    "project-context", "project-memory",
                                                                    "file-references", "per-message-write",
                                                                    "project-diff"],
                                                       "providers": getattr(self.server, "provider_status", {})})
            if path == "/api/agents":
                from .agents import AgentRegistry
                reg = getattr(self.server, "registry", None) or AgentRegistry(self.engine)
                return self._send_json(HTTPStatus.OK, {"agents": reg.list(),
                                                       "secret_storage": reg.secret_storage_status()})
            m_messages = re.fullmatch(r"/api/tasks/([^/]+)/messages", path)
            if m_messages:
                return self._send_json(200, {"messages": self.engine.messages(m_messages.group(1)),
                                             "chat": self.engine.chat_state(m_messages.group(1))})

            m_jobs = re.fullmatch(r"/api/tasks/([^/]+)/planning-jobs", path)
            if m_jobs:
                return self._send_json(200, {"jobs": self.server.planning_jobs.list(m_jobs.group(1))})
            m_execution = re.fullmatch(r"/api/tasks/([^/]+)/execution-jobs", path)
            if m_execution:
                return self._send_json(200, {"jobs": self.server.execution_jobs.list(m_execution.group(1))})
            m_team_jobs = re.fullmatch(r"/api/tasks/([^/]+)/team-jobs", path)
            if m_team_jobs:
                return self._send_json(200, {"jobs": self.server.team_jobs.list(m_team_jobs.group(1))})
            m_approval = re.fullmatch(r"/api/tasks/([^/]+)/approval", path)
            if m_approval:
                return self._send_json(200, self.runtime.approval(m_approval.group(1)))

            # 2. List projects
            if path == "/api/projects":
                projects = self.engine.projects_list()
                return self._send_json(HTTPStatus.OK, {"projects": projects})

            # 3. Project detail & doctor
            m_proj = re.match(r"^/api/projects/([^/]+)$", path)
            if m_proj:
                proj_id = m_proj.group(1)
                doc = self.engine.doctor(proj_id)
                return self._send_json(HTTPStatus.OK, doc)

            m_proj_files = re.fullmatch(r"/api/projects/([^/]+)/files", path)
            if m_proj_files:
                from .workspace import project_files_snapshot
                doc = self.engine.doctor(m_proj_files.group(1))
                snapshot = project_files_snapshot(doc["project"]["path"], limit=250)
                return self._send_json(HTTPStatus.OK, {"project_id": doc["project"]["id"], **snapshot})

            m_proj_memory = re.fullmatch(r"/api/projects/([^/]+)/memory", path)
            if m_proj_memory:
                doc = self.engine.doctor(m_proj_memory.group(1))
                return self._send_json(HTTPStatus.OK, {"project_id": doc["project"]["id"],
                                                       "memory": doc["project"].get("memory", "")})

            m_proj_file = re.fullmatch(r"/api/projects/([^/]+)/file", path)
            if m_proj_file:
                from .workspace import read_project_files
                requested = (query.get("path") or [""])[0]
                doc = self.engine.doctor(m_proj_file.group(1))
                found = read_project_files(doc["project"]["path"], [requested])
                if not found:
                    raise CollabError("NOT_FOUND",
                                      "File not found, not readable, or outside the project workspace")
                return self._send_json(HTTPStatus.OK,
                                       {"project_id": doc["project"]["id"], **found[0]})

            m_proj_diff = re.fullmatch(r"/api/projects/([^/]+)/diff", path)
            if m_proj_diff:
                from .workspace import project_diff
                doc = self.engine.doctor(m_proj_diff.group(1))
                return self._send_json(HTTPStatus.OK,
                                       {"project_id": doc["project"]["id"], **project_diff(doc["project"]["path"])})

            # 4. List tasks for a project
            m_proj_tasks = re.match(r"^/api/projects/([^/]+)/tasks$", path)
            if m_proj_tasks:
                proj_id = m_proj_tasks.group(1)
                tasks = self.engine.tasks_list(proj_id)
                return self._send_json(HTTPStatus.OK, {"tasks": tasks})

            # 5. List all tasks (cross-project overview)
            if path == "/api/tasks":
                proj_id = query.get("project", [None])[0]
                tasks = self.engine.tasks_list(proj_id)
                return self._send_json(HTTPStatus.OK, {"tasks": tasks})

            # 6. Task detail (show)
            m_task = re.match(r"^/api/tasks/([^/]+)$", path)
            if m_task:
                task_id = m_task.group(1)
                task_data = self.engine.show(task_id)
                return self._send_json(HTTPStatus.OK, task_data)

            # 7. Task runs list
            m_runs = re.match(r"^/api/tasks/([^/]+)/runs$", path)
            if m_runs:
                task_id = m_runs.group(1)
                runs_data = self.runtime.list(task_id)
                return self._send_json(HTTPStatus.OK, runs_data)

            # 8. Template generator
            m_tpl = re.match(r"^/api/tasks/([^/]+)/template/([^/]+)$", path)
            if m_tpl:
                task_id, dispatch_id = m_tpl.group(1), m_tpl.group(2)
                tpl = self.engine.template(task_id, dispatch_id)
                return self._send_json(HTTPStatus.OK, {"template": tpl})

            # 9. Handoff bundle
            m_handoff = re.match(r"^/api/tasks/([^/]+)/handoff/([^/]+)$", path)
            if m_handoff:
                task_id, dispatch_id = m_handoff.group(1), m_handoff.group(2)
                t = self.engine.show(task_id)
                d = self.engine._get_dispatch(t, dispatch_id)
                tpl = self.engine.template(task_id, dispatch_id)
                handoff_bundle = {
                    "task": {"id": t["id"], "title": t["title"], "requirement": t["requirement"], "phase": t["phase"]},
                    "dispatch": d,
                    "template": tpl,
                    "instructions": f"Execute work for {d['kind']} (role: {d['role']}). Submit final report matching template.",
                }
                return self._send_json(HTTPStatus.OK, handoff_bundle)

            # 10. Run artifact safe viewer
            m_art = re.match(r"^/api/runs/([^/]+)/artifacts/([^/]+)$", path)
            if m_art:
                run_id, filename = m_art.group(1), m_art.group(2)
                if filename not in SAFE_ARTIFACT_NAMES:
                    return self._send_error(HTTPStatus.FORBIDDEN, "FORBIDDEN", f"Access to '{filename}' disallowed")
                if not re.fullmatch(r"[0-9a-f]{32}", run_id):
                    return self._send_error(400, "INVALID_INPUT", "Invalid run ID")
                with self.engine.transaction() as db:
                    self.runtime._get(db, run_id)
                runs_root = Path(self.engine.path).parent / "runs" / run_id
                target_file = (runs_root / filename).resolve()
                # Directory traversal guard
                if not target_file.is_relative_to(runs_root.resolve()):
                    return self._send_error(HTTPStatus.FORBIDDEN, "FORBIDDEN", "Path traversal prohibited")
                if not target_file.is_file():
                    return self._send_error(HTTPStatus.NOT_FOUND, "NOT_FOUND", f"Artifact '{filename}' does not exist")
                size = target_file.stat().st_size
                tail = filename in ("events.jsonl", "stderr.log") and size > ARTIFACT_PREVIEW_LIMIT
                with target_file.open("rb") as stream:
                    if tail:
                        stream.seek(size - ARTIFACT_PREVIEW_LIMIT)
                    raw = stream.read(ARTIFACT_PREVIEW_LIMIT)
                return self._send_json(HTTPStatus.OK, {"filename": filename, "run_id": run_id,
                                                       "content": raw.decode("utf-8", errors="replace"),
                                                       "size_bytes": size, "truncated": size > len(raw),
                                                       "view": "tail" if tail else "head"})

            # 11. Static files or SPA index
            if not path.startswith("/api/"):
                return self._serve_static(path)

            return self._send_error(HTTPStatus.NOT_FOUND, "NOT_FOUND", f"Route not found: {path}")

        except CollabError as err:
            status = HTTPStatus.BAD_REQUEST
            if err.code == "NOT_FOUND":
                status = HTTPStatus.NOT_FOUND
            elif err.code in ("CONFLICT", "ALREADY_STARTED"):
                status = HTTPStatus.CONFLICT
            elif err.code == "INVALID_STATE":
                status = HTTPStatus.CONFLICT
            return self._send_error(status, err.code, str(err))
        except Exception as ex:
            return self._send_error(HTTPStatus.INTERNAL_SERVER_ERROR, "INTERNAL_ERROR", str(ex))

    def do_POST(self):
        if not self._local_request():
            return
        if self.headers.get("Content-Length", "0") != "0" and self.headers.get("Content-Type", "").split(";")[0] != "application/json":
            return self._send_error(415, "INVALID_INPUT", "application/json required")
        parsed = urllib.parse.urlparse(self.path)
        path = parsed.path.rstrip("/")

        try:
            body = self._read_json()
            if path == "/api/agents":
                from .agents import AgentRegistry
                reg = getattr(self.server, "registry", None) or AgentRegistry(self.engine)
                created = reg.register(body)
                return self._send_json(HTTPStatus.CREATED, created)
            m_agent_probe = re.fullmatch(r"/api/agents/([a-z][a-z0-9-]{0,63})/probe", path)
            m_agent_enabled = re.fullmatch(r"/api/agents/([a-z][a-z0-9-]{0,63})/enabled", path)
            m_agent_profile = re.fullmatch(r"/api/agents/([a-z][a-z0-9-]{0,63})/profile", path)
            m_agent_delete = re.fullmatch(r"/api/agents/([a-z][a-z0-9-]{0,63})/delete", path)
            if m_agent_enabled:
                if set(body) != {"enabled"}:
                    raise CollabError("INVALID_INPUT", "enabled boolean required")
                from .agents import AgentRegistry
                return self._send_json(200, AgentRegistry(self.engine).set_enabled(m_agent_enabled.group(1), body["enabled"]))
            if m_agent_profile:
                from .agents import AgentRegistry
                return self._send_json(200, AgentRegistry(self.engine).update_profile(m_agent_profile.group(1), body))
            if m_agent_delete:
                from .agents import AgentRegistry
                removed = AgentRegistry(self.engine).remove(
                    m_agent_delete.group(1), cancel_queued=bool(body.get("cancel_queued", True))
                )
                return self._send_json(200, {"removed": removed, "deleted": removed, "agent_id": m_agent_delete.group(1)})
            if m_agent_probe:
                if body:
                    raise CollabError("INVALID_INPUT", "Connection probe takes no task data")
                from .agents import AgentRegistry
                from .openai_runtime import probe_openai_compatible
                reg = getattr(self.server, "registry", None) or AgentRegistry(self.engine)
                agent = reg.get(m_agent_probe.group(1))
                if agent is None:
                    raise CollabError("NOT_FOUND", "Agent not found")
                return self._send_json(HTTPStatus.OK, probe_openai_compatible(agent))
            m_messages = re.fullmatch(r"/api/tasks/([^/]+)/messages", path)
            if m_messages:
                allowed_keys = {"message_id", "recipient", "content", "allow_write", "context_files", "referenced_task_ids", "rounds", "tool_files"}
                if not {"message_id", "recipient", "content"}.issubset(set(body)) or set(body) - allowed_keys:
                    raise CollabError("INVALID_INPUT", "message_id, recipient and content required")
                return self._send_json(202, self.engine.message_add(m_messages.group(1), **body))
            m_msg_resolve = re.fullmatch(r"/api/tasks/([^/]+)/messages/([^/]+)/resolve", path)
            if m_msg_resolve:
                action = body.get("action", "skip")
                return self._send_json(200, self.engine.message_resolve(m_msg_resolve.group(1), m_msg_resolve.group(2), action=action))

            # Group chat session settings and the explicit "continue" action for
            # agent-to-agent messages held back by the automatic round budget.
            m_chat_config = re.fullmatch(r"/api/tasks/([^/]+)/chat-config", path)
            if m_chat_config:
                return self._send_json(200, self.engine.chat_config(m_chat_config.group(1), body))
            m_chat_release = re.fullmatch(r"/api/tasks/([^/]+)/chat-release", path)
            if m_chat_release:
                return self._send_json(200, self.engine.chat_release(m_chat_release.group(1)))

            m_revision = re.fullmatch(r"/api/tasks/([^/]+)/revisions", path)
            if m_revision:
                if set(body) != {"message_id", "expected_revision", "requirement"}:
                    raise CollabError("INVALID_INPUT", "message_id, expected_revision and requirement are required")
                return self._send_json(200, self.engine.revise(m_revision.group(1), **body))

            m_approve = re.fullmatch(r"/api/tasks/([^/]+)/approve", path)
            if m_approve:
                if set(body) != {"digest"} or not isinstance(body["digest"], str):
                    raise CollabError("INVALID_INPUT", "Reviewed plan digest is required")
                return self._send_json(200, self.runtime.approval(m_approve.group(1), body["digest"]))

            m_team = re.fullmatch(r"/api/tasks/([^/]+)/team", path)
            if m_team:
                if set(body) - {"members", "coordinator", "reviewer"} or "members" not in body:
                    raise CollabError("INVALID_INPUT", "Team members and optional coordinator/reviewer required")
                return self._send_json(200, self.engine.configure_team(m_team.group(1), **body))

            m_discuss = re.fullmatch(r"/api/tasks/([^/]+)/discuss", path)
            if m_discuss:
                if set(body) - {"timeout"}:
                    raise CollabError("INVALID_INPUT", "Only timeout is accepted; providers are configured by server owner")
                job = self.server.planning_jobs.start(m_discuss.group(1), body.get("timeout", 300))
                return self._send_json(202, job)

            m_execute = re.fullmatch(r"/api/tasks/([^/]+)/execute", path)
            if m_execute:
                if body:
                    raise CollabError("INVALID_INPUT", "Execution uses a fixed six-call budget")
                return self._send_json(202, self.server.execution_jobs.start(m_execute.group(1)))

            m_auto_team = re.fullmatch(r"/api/tasks/([^/]+)/delegate-team", path)
            if m_auto_team:
                if set(body) != {"authorize_execution"} or body["authorize_execution"] is not True:
                    raise CollabError("APPROVAL_REQUIRED", "Explicit authorization to discuss AND execute is required")
                return self._send_json(202, self.server.team_jobs.start(m_auto_team.group(1)))

            m_recover = re.fullmatch(r"/api/tasks/([^/]+)/(execution|planning|team)-jobs/([^/]+)/recover", path)
            if m_recover:
                if body:
                    raise CollabError("INVALID_INPUT", "Recovery does not accept overrides")
                manager = getattr(self.server, m_recover.group(2) + "_jobs")
                return self._send_json(200, manager.recover(m_recover.group(1), m_recover.group(3)))

            # Project memory, revert-file, delete
            m_proj_memory = re.fullmatch(r"/api/projects/([^/]+)/memory", path)
            if m_proj_memory:
                allowed = {"memory", "append"}
                if set(body) - allowed or "memory" not in body:
                    raise CollabError("INVALID_INPUT", "memory is required")
                if body.get("append"):
                    project = self.engine.doctor(m_proj_memory.group(1))["project"]
                    existing = project.get("memory", "")
                    merged = (existing + "\n" + body["memory"]).strip() if existing else body["memory"]
                    updated = self.engine.project_memory_update(m_proj_memory.group(1), merged)
                else:
                    updated = self.engine.project_memory_update(m_proj_memory.group(1), body["memory"])
                return self._send_json(200, {"project_id": updated["id"], "memory": updated.get("memory", "")})
            m_proj_revert = re.fullmatch(r"/api/projects/([^/]+)/revert-file", path)
            if m_proj_revert:
                from .workspace import revert_project_file
                doc = self.engine.doctor(m_proj_revert.group(1))
                result = revert_project_file(doc["project"]["path"], body.get("path", ""))
                return self._send_json(200, {"reverted": True, "path": result["reverted"], "mode": result["mode"]})
            m_proj_delete = re.fullmatch(r"/api/projects/([^/]+)/delete", path)
            if m_proj_delete:
                result = self.engine.project_delete(m_proj_delete.group(1))
                return self._send_json(200, {"deleted": True, "project_id": result["deleted"],
                                             "tasks_removed": result["tasks_removed"]})

            # Task rename, delete
            m_task_rename = re.fullmatch(r"/api/tasks/([^/]+)/rename", path)
            if m_task_rename:
                return self._send_json(200, self.engine.task_rename(m_task_rename.group(1), body.get("title", "")))
            m_task_delete = re.fullmatch(r"/api/tasks/([^/]+)/delete", path)
            if m_task_delete:
                result = self.engine.task_delete(m_task_delete.group(1))
                return self._send_json(200, {"deleted": True, "task_id": result["deleted"]})

            # 1. Project Add
            if path == "/api/projects":
                name = body.get("name")
                proj_path = body.get("path")
                project = self.engine.project_add(name, proj_path)
                return self._send_json(HTTPStatus.CREATED, project)

            # 2. Task Add
            if path == "/api/tasks":
                proj_id = body.get("project_id")
                title = body.get("title")
                req = body.get("requirement")
                task = self.engine.task_add(proj_id, title, req)
                return self._send_json(HTTPStatus.CREATED, task)

            # 3. Dispatch
            m_dispatch = re.match(r"^/api/tasks/([^/]+)/dispatch$", path)
            if m_dispatch:
                task_id = m_dispatch.group(1)
                result = self.engine.dispatch(task_id)
                return self._send_json(HTTPStatus.OK, result)

            # 4. Submit Report
            m_submit = re.match(r"^/api/tasks/([^/]+)/submit$", path)
            if m_submit:
                task_id = m_submit.group(1)
                report = body.get("report") if "report" in body else body
                result = self.engine.submit(task_id, report)
                return self._send_json(HTTPStatus.OK, result)

            # 5. Pause
            m_pause = re.match(r"^/api/tasks/([^/]+)/pause$", path)
            if m_pause:
                task_id = m_pause.group(1)
                result = self.engine.pause(task_id)
                return self._send_json(HTTPStatus.OK, result)

            # 6. Resume
            m_resume = re.match(r"^/api/tasks/([^/]+)/resume$", path)
            if m_resume:
                task_id = m_resume.group(1)
                result = self.engine.resume(task_id)
                return self._send_json(HTTPStatus.OK, result)

            # 7. Retry
            m_retry = re.match(r"^/api/tasks/([^/]+)/retry$", path)
            if m_retry:
                task_id = m_retry.group(1)
                dispatch_id = body.get("dispatch_id")
                result = self.engine.retry(task_id, dispatch_id)
                return self._send_json(HTTPStatus.OK, result)

            # 8. Expire
            m_expire = re.match(r"^/api/tasks/([^/]+)/expire$", path)
            if m_expire:
                task_id = m_expire.group(1)
                result = self.engine.expire(task_id)
                return self._send_json(HTTPStatus.OK, result)

            # 9. Reserve synchronously, then execute the dispatch's own provider.
            m_run = re.match(r"^/api/tasks/([^/]+)/run$", path)
            if m_run:
                task_id = m_run.group(1)
                dispatch_id = body.get("dispatch_id")
                timeout = body.get("timeout", 300)
                if set(body) - {"dispatch_id", "timeout"}:
                    raise CollabError("INVALID_INPUT", "Unknown execution options")
                task = self.engine.show(task_id)
                dispatch = self.engine._get_dispatch(task, dispatch_id)
                if dispatch["kind"] in ("CODE", "ANALYZE", "FIX", "VERIFY") and not self.runtime.approval(task_id)["approved"]:
                    raise CollabError("APPROVAL_REQUIRED", "Approve the current plan before execution")
                if dispatch["role"] == "codex":
                    runtime = self.runtime
                elif dispatch["role"] == "antigravity":
                    runtime = self.server.antigravity_runtime
                else:
                    from .agents import AgentRegistry
                    registry = getattr(self.server, "registry", None) or AgentRegistry(self.engine)
                    runtime = registry.get_runtime(dispatch["role"])
                reserved, dispatch = runtime._reserve(task_id, dispatch_id, timeout)

                # Background thread execution
                def _worker():
                    try:
                        runtime.execute_reserved(reserved, dispatch)
                    except Exception as err:
                        sys.stderr.write(f"[Runtime Worker Error] task={task_id} dispatch={dispatch_id}: {err}\n")

                t = threading.Thread(target=_worker, daemon=True, name=f"CodexRunner-{dispatch_id}")
                try:
                    t.start()
                except Exception as err:
                    reserved.update(status="FAILED", error=str(err))
                    runtime._fail(reserved)
                    raise
                return self._send_json(HTTPStatus.ACCEPTED, {
                    "status": "ACCEPTED",
                    "task_id": task_id,
                    "dispatch_id": dispatch_id,
                    "run_id": reserved["id"],
                    "provider": runtime.provider,
                    "message": "Execution reserved; inspect run status for actual result",
                })

            # 10. Demo Task Creation (Explicitly Simulated)
            if path == "/api/demo":
                proj_name = body.get("name", "Demo Workspace")
                proj_path = body.get("path", str(Path(self.engine.path).parent))
                p = self.engine.project_add(proj_name, proj_path)
                t = self.engine.task_add(p["id"], "演示任务 (SIMULATED)", "模拟双 Agent 协作全流程")
                task_id = t["id"]
                adapter = MockAdapter()
                # Run complete simulation
                for _ in range(3):
                    d = self.engine.dispatch(task_id)["dispatch"]
                    if d:
                        rep = adapter.prepare(d, self.engine.template(task_id, d["id"]))
                        self.engine.submit(task_id, rep)
                d_code = self.engine.dispatch(task_id)["dispatch"]
                if d_code:
                    self.engine.submit(task_id, adapter.prepare(d_code, self.engine.template(task_id, d_code["id"])))
                d_ver = self.engine.dispatch(task_id)["dispatch"]
                if d_ver:
                    self.engine.submit(task_id, adapter.prepare(d_ver, self.engine.template(task_id, d_ver["id"])))
                return self._send_json(HTTPStatus.CREATED, {
                    "status": "SIMULATED_COMPLETED",
                    "task": self.engine.show(task_id),
                })

            return self._send_error(HTTPStatus.NOT_FOUND, "NOT_FOUND", f"Route not found: {path}")

        except CollabError as err:
            status = HTTPStatus.BAD_REQUEST
            if err.code == "NOT_FOUND":
                status = HTTPStatus.NOT_FOUND
            elif err.code in ("CONFLICT", "ALREADY_STARTED"):
                status = HTTPStatus.CONFLICT
            elif err.code == "INVALID_STATE":
                status = HTTPStatus.CONFLICT
            return self._send_error(status, err.code, str(err))
        except Exception as ex:
            return self._send_error(HTTPStatus.INTERNAL_SERVER_ERROR, "INTERNAL_ERROR", str(ex))

    def _serve_static(self, req_path):
        if not self.static_dir or not os.path.isdir(self.static_dir):
            return self._send_error(HTTPStatus.NOT_FOUND, "NOT_FOUND", "Static assets directory not configured")

        clean_path = req_path.lstrip("/")
        if not clean_path or clean_path == "/":
            clean_path = "index.html"

        target = (Path(self.static_dir) / clean_path).resolve()
        # Security: protect against directory traversal
        if not target.is_relative_to(Path(self.static_dir).resolve()):
            return self._send_error(HTTPStatus.FORBIDDEN, "FORBIDDEN", "Directory traversal prohibited")

        # SPA fallback: if file doesn't exist and request doesn't have an extension, serve index.html
        if not target.is_file():
            if "." not in clean_path:
                target = Path(self.static_dir) / "index.html"
            else:
                return self._send_error(HTTPStatus.NOT_FOUND, "NOT_FOUND", f"File not found: {clean_path}")

        if not target.is_file():
            return self._send_error(HTTPStatus.NOT_FOUND, "NOT_FOUND", "index.html not found")

        mime, _ = mimetypes.guess_type(str(target))
        if not mime:
            mime = "text/plain"
        if mime.startswith("text/") or mime in ("application/javascript", "application/json"):
            mime += "; charset=utf-8"

        content = target.read_bytes()
        self.send_response(HTTPStatus.OK)
        self.send_header("Content-Type", mime)
        self.send_header("Content-Length", str(len(content)))
        self._cors_headers()
        self.end_headers()
        self.wfile.write(content)


def create_server(db_path, host="127.0.0.1", port=8765, static_dir=None, executable="codex",
                  antigravity_executable="agy", antigravity_model="gemini-3.8-flash-high"):
    if host not in ("127.0.0.1", "localhost", "::1"):
        raise CollabError("INVALID_INPUT", "Only loopback binding is supported")
    antigravity_executable = resolve_antigravity_executable(antigravity_executable)
    executable = resolve_codex_executable(executable)
    engine = Engine(db_path)
    from .agents import AgentRegistry
    registry = AgentRegistry(engine)
    runtime = Runtime(engine, executable=executable)
    runtime.require_approval = True
    server = ThreadingHTTPServer((host, port), WorkTogetherHandler)
    server.engine = engine
    server.registry = registry
    server.runtime = runtime
    server.antigravity_runtime = AntigravityRuntime(engine, antigravity_executable, antigravity_model)
    server.provider_status = {
        "codex": {"available": shutil.which(executable) is not None, "executable": executable},
        "antigravity": {"available": shutil.which(antigravity_executable) is not None,
                        "executable": antigravity_executable},
    }
    server.planning_jobs = PlanningJobs(engine, runtime, server.antigravity_runtime)
    from .jobs import ExecutionJobs
    server.execution_jobs = ExecutionJobs(engine, runtime, server.antigravity_runtime)
    from .auto_team import TeamJobs
    server.team_jobs = TeamJobs(engine, runtime, server.antigravity_runtime)
    server.chat_queue = ChatQueue(engine, runtime, server.antigravity_runtime)
    if static_dir is None:
        # Default static dir is next to server.py in web/static or static
        static_dir = Path(__file__).parent / "static"
    server.static_dir = str(Path(static_dir).resolve())
    return server


def run_server(db_path, host="127.0.0.1", port=8765, static_dir=None, **providers):
    server = create_server(db_path, host=host, port=port, static_dir=static_dir, **providers)
    print("===========================================================", flush=True)
    print(f"🚀 WorkTogether Web Server running at http://{host}:{port}/", flush=True)
    print(f"📁 SQLite Database: {Path(db_path).resolve()}", flush=True)
    print(f"🌐 Static Web Dir:  {server.static_dir}", flush=True)
    for name, info in server.provider_status.items():
        print(f"🤖 {name}: {'CLI found' if info['available'] else 'CLI missing'} ({info['executable']})", flush=True)
    print("===========================================================", flush=True)
    try:
        server.chat_queue.start()
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nStopping server...")
        server.shutdown()
        server.server_close()


if __name__ == "__main__":
    db = sys.argv[1] if len(sys.argv) > 1 else ".collab/state.sqlite3"
    port = int(sys.argv[2]) if len(sys.argv) > 2 else 8765
    run_server(db, port=port)
