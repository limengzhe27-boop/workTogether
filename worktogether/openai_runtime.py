"""OpenAI-compatible HTTP API adapter for WorkTogether.

Connects OpenAI-compatible endpoints for conversation and read-only analysis.
No fabricated fallback results or code-writing claims.
"""
import json
import os
import urllib.error
import urllib.request
from pathlib import Path

from .core import CollabError, encode, require, uid
from .agents import local_endpoint
from .runtime import Runtime


class OpenAICompatibleRuntime(Runtime):
    provider = "openai_compat"

    def __init__(self, engine, agent_config):
        super().__init__(engine, executable="http")
        self.agent = agent_config
        self.role = agent_config.id
        self.endpoint = agent_config.endpoint.rstrip("/")
        self.model = agent_config.model
        self.api_key_env = agent_config.api_key_env

    def get_api_key(self) -> str:
        return os.environ.get(self.api_key_env, "") if self.api_key_env else ""

    def command(self, run, dispatch):
        # HTTP adapter does not spawn an external CLI subprocess; returns target endpoint info
        return ["http-post", f"{self.endpoint}/chat/completions", "--model", self.model]

    def execute_reserved(self, run, d):
        if d["kind"] not in ("CHAT", "ANALYZE"):
            run.update(status="FAILED", error="API agents support conversation and read-only analysis only")
            self._fail(run)
            return run
        task, dispatch_id = run["task_id"], run["dispatch_id"]
        root = Path(run["artifact_dir"])
        root.mkdir(parents=True, exist_ok=True)

        prompt = self._prompt(d)
        (root / "prompt.txt").write_text(prompt, encoding="utf-8")
        args = self.command(run, d)
        (root / "command.json").write_text(encode(args), encoding="utf-8")

        api_key = self.get_api_key()
        session_id = run.get("session_id") or uid()
        run["session_id"] = session_id

        # Mark RUNNING in database
        with self.engine.transaction() as db:
            current = self._get(db, run["id"])
            require(current["status"] == "PREPARED", "Run status conflict", "INVALID_STATE")
            run["status"] = "RUNNING"
            self._save(db, run)

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
            payload = {
                "model": self.model,
                "messages": [
                    {
                        "role": "system",
                        "content": (
                            f"You are {self.agent.name}, an AI collaborator in the WorkTogether platform. "
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
                with urllib.request.urlopen(req, timeout=run.get("timeout", 120)) as resp:
                    raw = resp.read(2_000_001)
                    require(len(raw) <= 2_000_000, "Provider response exceeds 2 MB", "PROVIDER_FAILED")
                    resp_data = json.loads(raw.decode("utf-8"))
                    choice = resp_data.get("choices", [{}])[0]
                    content = choice.get("message", {}).get("content", "").strip()
                    usage = resp_data.get("usage", {})
                    # Clean markdown code blocks if wrapped
                    if content.startswith("```"):
                        lines = content.splitlines()
                        if lines[0].startswith("```"):
                            lines = lines[1:]
                        if lines and lines[-1].startswith("```"):
                            lines = lines[:-1]
                        content = "\n".join(lines).strip()
                    report = json.loads(content)
            except (urllib.error.URLError, ValueError, KeyError, IndexError, TimeoutError, CollabError) as exc:
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

    def read_result(self, root):
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
