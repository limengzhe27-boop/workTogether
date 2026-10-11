"""OpenAI-compatible HTTP API adapter for WorkTogether.

Connects OpenAI-compatible endpoints for conversation and read-only analysis.
No fabricated fallback results or code-writing claims.
"""
import json
import os
import time
import urllib.error
import urllib.request
from pathlib import Path

from .core import CollabError, encode, require, uid
from .agents import local_endpoint
from .runtime import Runtime


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    """Never forward task content or credentials to a different URL."""

    def redirect_request(self, request, fp, code, msg, headers, newurl):
        return None


def _open_no_redirect(request, timeout):
    return urllib.request.build_opener(_NoRedirect()).open(request, timeout=timeout)


def probe_openai_compatible(agent):
    """Explicit, bounded, task-free model call to check a configured API agent."""
    require(agent.type == "openai_compat" and agent.enabled, "API agent is not enabled", "UNSUPPORTED_PROVIDER")
    key = agent.resolve_api_key() if hasattr(agent, "resolve_api_key") else (
        os.environ.get(agent.api_key_env, "") if agent.api_key_env else ""
    )
    if not local_endpoint(agent.endpoint) and not key:
        return {"agent_id": agent.id, "connected": False, "code": "MISSING_KEY",
                "message": "服务端尚未配置 API Key"}
    payload = {"model": agent.model,
               "messages": [{"role": "user", "content": "Connection check only. Reply with PONG."}],
               "temperature": 0.2, "max_tokens": 16}
    headers = {"Content-Type": "application/json"}
    if key:
        headers["Authorization"] = "Bearer " + key
    request = urllib.request.Request(agent.endpoint.rstrip("/") + "/chat/completions",
                                     data=json.dumps(payload).encode("utf-8"), headers=headers,
                                     method="POST")
    try:
        with _open_no_redirect(request, timeout=10) as response:
            raw = response.read(65_537)
        if len(raw) > 65_536:
            return {"agent_id": agent.id, "connected": False, "code": "INCOMPATIBLE_RESPONSE",
                    "message": "接口响应超过测试大小上限"}
        result = json.loads(raw.decode("utf-8"))
        content = result["choices"][0]["message"]["content"]
        if not isinstance(content, str) or not content.strip():
            raise ValueError("Empty chat completion")
        raw_usage = result.get("usage")
        usage = ({name: raw_usage[name] for name in
                  ("prompt_tokens", "completion_tokens", "total_tokens", "input_tokens", "output_tokens")
                  if name in raw_usage and type(raw_usage[name]) is int and raw_usage[name] >= 0}
                 if isinstance(raw_usage, dict) else None)
        return {"agent_id": agent.id, "connected": True, "code": "OK",
                "message": "接口已返回聊天响应；实际任务仍需验证",
                "usage": usage}
    except urllib.error.HTTPError as exc:
        return {"agent_id": agent.id, "connected": False, "code": "HTTP_ERROR",
                "message": "接口返回 HTTP " + str(exc.code)}
    except (urllib.error.URLError, TimeoutError, OSError):
        return {"agent_id": agent.id, "connected": False, "code": "NETWORK_ERROR",
                "message": "10 秒内无法连接接口"}
    except (ValueError, KeyError, IndexError, TypeError, AttributeError):
        return {"agent_id": agent.id, "connected": False, "code": "INCOMPATIBLE_RESPONSE",
                "message": "接口未返回可用的聊天响应"}


class OpenAICompatibleRuntime(Runtime):
    provider = "openai_compat"

    def __init__(self, engine, agent_config):
        super().__init__(engine, executable="http")
        self.agent = agent_config
        self.role = agent_config.id
        self.endpoint = agent_config.endpoint.rstrip("/")
        self.model = agent_config.model
        self.api_key_env = agent_config.api_key_env
        self.system_prompt = getattr(agent_config, "system_prompt", "")
        self.require_approval = True

    def get_api_key(self) -> str:
        if hasattr(self.agent, "resolve_api_key"):
            return self.agent.resolve_api_key()
        return os.environ.get(self.api_key_env, "") if self.api_key_env else ""

    def command(self, run, dispatch):
        # HTTP adapter does not spawn an external CLI subprocess; returns target endpoint info
        return ["http-post", f"{self.endpoint}/chat/completions", "--model", self.model]

    def execute_reserved(self, run, d):
        if d["kind"] not in ("CHAT", "ANALYZE", "PLAN", "PLAN_REVIEW", "PLAN_FINALIZE", "CONSULT", "CLAIM"):
            run.update(status="FAILED", error="API agents support conversation, supplied-context planning and read-only analysis only")
            self._fail(run)
            return run
        task, dispatch_id = run["task_id"], run["dispatch_id"]
        root = Path(run["artifact_dir"])
        root.mkdir(parents=True, exist_ok=True)

        prompt = ("You have no repository, shell, browser or filesystem tools. Reason only "
                  "from supplied context; label assumptions and missing evidence. Never "
                  "claim that you inspected files or executed tests.\n" + self._prompt(d))
        (root / "prompt.txt").write_text(prompt, encoding="utf-8")
        args = self.command(run, d)
        (root / "command.json").write_text(encode(args), encoding="utf-8")

        api_key = self.get_api_key()
        session_id = run.get("session_id") or uid()
        run["session_id"] = session_id

        # The reservation may have become stale while the prompt was prepared.
        try:
            with self.engine.transaction() as db:
                task_state = self.engine._task(db, task)
                current_dispatch = self.engine._get_dispatch(task_state, dispatch_id)
                current = self._get(db, run["id"])
                require((task_state["status"] == "ACTIVE" or
                         (task_state["status"] == "COMPLETED" and d["kind"] == "CHAT"))
                        and current_dispatch["status"] == "ACTIVE"
                        and current_dispatch["deadline"] > time.time()
                        and current["status"] == "PREPARED",
                        "Task stopped before API request", "INVALID_STATE")
                if self.require_approval and d["kind"] == "ANALYZE":
                    approval = db.execute("SELECT digest FROM approvals WHERE task_id=?", (task,)).fetchone()
                    require(approval is not None and approval[0] == self.plan_digest(task_state),
                            "Approve the current plan before analysis", "APPROVAL_REQUIRED")
                run["status"] = "RUNNING"
                self._save(db, run)
        except CollabError as exc:
            run.update(status="FAILED", error=str(exc), error_code=exc.code)
            self._fail(run)
            return run

        report = None
        usage = None
        err_msg = ""

        if not api_key and not local_endpoint(self.endpoint):
            run.update(status="FAILED", error="API key is not configured in the server environment")
            self._fail(run)
            return run
        else:
            # Live HTTP Call
            url = f"{self.endpoint}/chat/completions"
            persona_suffix = f" {self.system_prompt}" if self.system_prompt else ""
            payload = {
                "model": self.model,
                "messages": [
                    {
                        "role": "system",
                        "content": (
                            f"You are {self.agent.name}, an AI collaborator in the WorkTogether platform.{persona_suffix} "
                            "Follow all response instructions strictly. Return ONLY a valid JSON object matching the required schema."
                        ),
                    },
                    {"role": "user", "content": prompt},
                ],
                "temperature": 0.2,
            }

            req_body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
            headers = {"Content-Type": "application/json"}
            if api_key:
                headers["Authorization"] = f"Bearer {api_key}"
            req = urllib.request.Request(url, data=req_body, headers=headers, method="POST")

            try:
                with _open_no_redirect(req, timeout=run.get("timeout", 120)) as resp:
                    raw = resp.read(2_000_001)
                    require(len(raw) <= 2_000_000, "Provider response exceeds 2 MB", "PROVIDER_FAILED")
                    resp_data = json.loads(raw.decode("utf-8"))
                    choice = resp_data.get("choices", [{}])[0]
                    raw_content = choice.get("message", {}).get("content")
                    if not isinstance(raw_content, str) or not raw_content.strip():
                        raise ValueError("Empty chat completion content")
                    content = raw_content.strip()
                    usage = resp_data.get("usage", {})
                    if d["kind"] == "CHAT":
                        from .runtime import normalize_chat_report
                        report = normalize_chat_report(content, d)
                    else:
                        # Clean markdown code blocks if wrapped
                        if content.startswith("```"):
                            lines = content.splitlines()
                            if lines[0].startswith("```"):
                                lines = lines[1:]
                            if lines and lines[-1].startswith("```"):
                                lines = lines[:-1]
                            content = "\n".join(lines).strip()
                        report = json.loads(content)
            except (urllib.error.URLError, ValueError, TypeError, AttributeError,
                    KeyError, IndexError, TimeoutError, CollabError) as exc:
                err_msg = f"{type(exc).__name__}: {exc}"
                (root / "stderr.log").write_text(err_msg, encoding="utf-8")
                run["status"] = "FAILED"
                run["error"] = "OpenAI-compatible API call failed; inspect the run log"
                self._fail(run)
                return run

        # Write artifacts
        (root / "result.json").write_text(encode(report), encoding="utf-8")
        events = [
            {"event": "init", "conversation_id": session_id},
            {"event": "turn.completed", "usage": usage},
        ]
        with (root / "events.jsonl").open("w", encoding="utf-8") as f:
            for ev in events:
                f.write(encode(ev) + "\n")

        with (root / "stderr.log").open("a", encoding="utf-8") as f:
            f.write(err_msg)

        run["status"] = "EXITED"
        run["exit_code"] = 0
        with self.engine.transaction() as db:
            self._save(db, run)

        return self.collect(run["id"])

    def read_result(self, root, dispatch=None):
        session, usage = None, None
        with (root / "events.jsonl").open(encoding="utf-8") as stream:
            for line in stream:
                event = json.loads(line)
                if event.get("event") == "init":
                    session = event.get("conversation_id")
                elif event.get("event") == "turn.completed":
                    usage = event.get("usage")
        report = json.loads((root / "result.json").read_text(encoding="utf-8"))
        return session, True, report, usage
