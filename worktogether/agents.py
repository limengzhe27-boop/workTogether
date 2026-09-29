"""Validated registry for task-scoped conversation participants.

Custom agents use an OpenAI-compatible chat-completions endpoint. Arbitrary CLI
programs do not share the Codex protocol and are intentionally not accepted.
"""
import ipaddress
import json
import os
import re
from urllib.parse import urlsplit

from .core import CollabError, require, string


DEFAULT_AGENTS = [
    {"id": "codex", "name": "Codex", "type": "cli", "executable": "codex",
     "roles": ["code", "plan", "chat"], "color": "#6366f1", "avatar": "🤖",
     "description": "主力开发与规划", "enabled": True},
    {"id": "antigravity", "name": "Antigravity", "type": "cli", "executable": "agy",
     "roles": ["review", "verify", "chat", "analysis"], "color": "#10b981", "avatar": "🦅",
     "description": "方案挑战与独立验收", "enabled": True},
    {"id": "deepseek-hermes", "name": "DeepSeek（API 模板）", "type": "openai_compat",
     "endpoint": "https://api.deepseek.com/v1", "model": "deepseek-chat",
     "api_key_env": "DEEPSEEK_API_KEY", "roles": ["chat", "analysis"], "color": "#f59e0b",
     "avatar": "🧠", "description": "配置密钥后可参与任务讨论；不是本地 Harness", "enabled": True},
]


def local_endpoint(endpoint):
    host = urlsplit(endpoint).hostname
    try:
        return ipaddress.ip_address(host).is_loopback
    except (ValueError, TypeError):
        return host == "localhost"


def validate_endpoint(endpoint):
    require(string(endpoint) and len(endpoint) <= 2048, "Endpoint required", "INVALID_INPUT")
    try:
        url = urlsplit(endpoint)
        port = url.port
    except ValueError:
        raise CollabError("INVALID_INPUT", "Invalid endpoint")
    require(url.scheme in ("http", "https") and bool(url.hostname) and port != 0
            and not url.username and not url.password and not url.query and not url.fragment,
            "Invalid endpoint", "INVALID_INPUT")
    require(url.scheme == "https" or local_endpoint(endpoint),
            "Plain HTTP is only allowed for local endpoints", "INVALID_INPUT")
    require(not endpoint.rstrip("/").endswith("/chat/completions"),
            "Provide the API base URL, not /chat/completions", "INVALID_INPUT")


class AgentConfig:
    def __init__(self, data):
        self.id = data["id"]
        self.name = data["name"]
        self.type = data["type"]
        self.executable = data.get("executable", self.id)
        self.endpoint = data.get("endpoint", "")
        self.model = data.get("model", "")
        self.api_key_env = data.get("api_key_env", "")
        self.roles = data.get("roles", ["chat"]) if self.type == "cli" else ["chat", "analysis"]
        if self.id == "antigravity" and "analysis" not in self.roles:
            self.roles = [*self.roles, "analysis"]
        self.color = data.get("color", "#6366f1")
        self.avatar = data.get("avatar", "🤖")
        self.description = data.get("description", "")
        self.enabled = data.get("enabled", True)

    @property
    def ready(self):
        if not self.enabled:
            return False
        if self.type == "cli":
            return self.id in ("codex", "antigravity")  # Actual executable/login is checked at runtime.
        return local_endpoint(self.endpoint) or bool(self.api_key_env and os.environ.get(self.api_key_env))

    def to_dict(self):
        return {"id": self.id, "name": self.name, "type": self.type,
                "executable": self.executable if self.type == "cli" else None,
                "endpoint": self.endpoint, "model": self.model,
                "api_key_env": self.api_key_env, "roles": self.roles,
                "color": self.color, "avatar": self.avatar,
                "description": self.description, "enabled": self.enabled,
                "ready": self.ready,
                "has_key": bool(self.api_key_env and os.environ.get(self.api_key_env))}


class AgentRegistry:
    def __init__(self, engine):
        self.engine = engine
        with self.engine.transaction() as db:
            for item in DEFAULT_AGENTS:
                db.execute("INSERT OR IGNORE INTO agents(id,name,type,body) VALUES(?,?,?,?)",
                           (item["id"], item["name"], item["type"], json.dumps(item, ensure_ascii=False)))
            # Older prototypes accepted plaintext keys. Remove them from live rows;
            # SQLite history/backups may retain old bytes, so operators should rotate.
            for row in db.execute("SELECT id,body FROM agents"):
                data = json.loads(row["body"])
                if "api_key" in data:
                    del data["api_key"]
                    db.execute("UPDATE agents SET body=? WHERE id=?",
                               (json.dumps(data, ensure_ascii=False), row["id"]))

    def list(self, enabled_only=False):
        with self.engine.transaction() as db:
            agents = [AgentConfig(json.loads(r[0])).to_dict()
                      for r in db.execute("SELECT body FROM agents ORDER BY rowid")]
        return [a for a in agents if a["enabled"]] if enabled_only else agents

    def get(self, agent_id):
        with self.engine.transaction() as db:
            row = db.execute("SELECT body FROM agents WHERE id=?", (agent_id,)).fetchone()
        return AgentConfig(json.loads(row[0])) if row else None

    @staticmethod
    def _ensure_not_in_flight(db, agent_id):
        for row in db.execute("SELECT body FROM messages"):
            message = json.loads(row[0])
            if message["status"] in ("QUEUED", "RUNNING") and agent_id in message.get("participants", []):
                raise CollabError("INVALID_STATE", "Agent has an unfinished conversation")
        for row in db.execute("SELECT body FROM tasks"):
            task = json.loads(row[0])
            if task["phase"] == "EXECUTING" and any(
                    step.get("assignee") == agent_id and step["status"] != "COMPLETED"
                    for step in task["steps"]):
                raise CollabError("INVALID_STATE", "Agent owns unfinished work")

    def register(self, data):
        require(isinstance(data, dict), "Agent object required", "INVALID_INPUT")
        require("api_key" not in data, "Set API keys in the server environment, not the database", "INVALID_INPUT")
        agent_id = data.get("id")
        require(isinstance(agent_id, str) and re.fullmatch(r"[a-z][a-z0-9-]{0,63}", agent_id),
                "Agent ID must be lowercase letters, digits or hyphens", "INVALID_INPUT")
        require(agent_id not in ("codex", "antigravity"), "Built-in agents cannot be replaced", "INVALID_INPUT")
        require(data.get("type") == "openai_compat", "Custom agents currently require an OpenAI-compatible API", "INVALID_INPUT")
        require(string(data.get("name")) and len(data["name"]) <= 100 and string(data.get("model"))
                and len(data["model"]) <= 150, "Name and model required", "INVALID_INPUT")
        validate_endpoint(data.get("endpoint"))
        env = data.get("api_key_env", "")
        require(isinstance(env, str) and (not env or re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", env)),
                "Invalid API key environment variable", "INVALID_INPUT")
        require(type(data.get("enabled", True)) is bool, "enabled must be boolean", "INVALID_INPUT")
        for key in ("description", "avatar", "color"):
            require(isinstance(data.get(key, ""), str) and len(data.get(key, "")) <= 300,
                    "Invalid display field", "INVALID_INPUT")
        clean = {"id": agent_id, "name": data["name"].strip(), "type": "openai_compat",
                 "endpoint": data["endpoint"].rstrip("/"), "model": data["model"].strip(),
                 "api_key_env": env, "roles": ["chat", "analysis"], "enabled": data.get("enabled", True),
                 "color": data.get("color") or "#6366f1", "avatar": data.get("avatar") or "🤖",
                 "description": data.get("description") or ""}
        with self.engine.transaction() as db:
            if db.execute("SELECT id FROM agents WHERE id=?", (agent_id,)).fetchone():
                self._ensure_not_in_flight(db, agent_id)
            db.execute("INSERT OR REPLACE INTO agents VALUES(?,?,?,?)",
                       (agent_id, clean["name"], clean["type"], json.dumps(clean, ensure_ascii=False)))
        return AgentConfig(clean).to_dict()

    def remove(self, agent_id):
        require(agent_id not in ("codex", "antigravity"), "Built-in agents cannot be removed", "INVALID_INPUT")
        with self.engine.transaction() as db:
            self._ensure_not_in_flight(db, agent_id)
            return db.execute("DELETE FROM agents WHERE id=?", (agent_id,)).rowcount > 0

    def get_runtime(self, agent_id, custom_executables=None):
        from .runtime import Runtime
        from .antigravity import AntigravityRuntime
        from .openai_runtime import OpenAICompatibleRuntime
        cfg = self.get(agent_id)
        require(cfg is not None and cfg.enabled, "Agent is not enabled", "UNSUPPORTED_PROVIDER")
        if agent_id == "codex":
            return Runtime(self.engine, executable=(custom_executables or {}).get("codex") or cfg.executable)
        if agent_id == "antigravity":
            return AntigravityRuntime(self.engine, executable=(custom_executables or {}).get("antigravity") or cfg.executable)
        require(cfg.type == "openai_compat" and cfg.ready, "Agent is not configured", "UNSUPPORTED_PROVIDER")
        return OpenAICompatibleRuntime(self.engine, cfg)
