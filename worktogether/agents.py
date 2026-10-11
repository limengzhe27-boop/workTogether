"""Validated registry for task-scoped conversation participants.

Custom agents use an OpenAI-compatible chat-completions endpoint. Arbitrary CLI
programs do not share the Codex protocol and are intentionally not accepted.
"""
import ipaddress
import json
import os
import re
import shutil
from pathlib import Path
from urllib.parse import urlsplit

from .core import CollabError, encode, require, string


DEFAULT_AGENTS = [
    {"id": "codex", "name": "Codex", "type": "cli", "executable": "codex",
     "roles": ["code", "plan", "chat", "analysis", "review", "verify"], "color": "#6366f1", "avatar": "🤖",
     "description": "本机 Codex CLI", "enabled": True},
    {"id": "antigravity", "name": "Antigravity", "type": "cli", "executable": "agy",
     "roles": ["review", "verify", "chat", "analysis", "plan"], "color": "#10b981", "avatar": "🦅",
     "description": "本机 Antigravity CLI", "enabled": True},
    {"id": "deepseek-hermes", "name": "DeepSeek API（云端接口）", "type": "openai_compat",
     "endpoint": "https://api.deepseek.com/v1", "model": "deepseek-chat",
     "api_key_env": "DEEPSEEK_API_KEY", "roles": ["chat", "analysis", "plan", "review"], "color": "#f59e0b",
     "avatar": "🧠", "description": "OpenAI 兼容接口", "enabled": True},
    {"id": "deepseek-harness", "name": "DeepSeek Harness（本机 CLI）", "type": "cli", "executable": "dsh",
     "roles": ["code", "plan", "chat", "analysis", "review", "verify"], "color": "#2563eb",
     "avatar": "🐋", "description": "本机 DeepSeek Harness CLI", "enabled": False},
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
    def __init__(self, data, stored_secret=""):
        self.id = data["id"]
        self.name = data["name"]
        self.type = data["type"]
        self.base_agent = data.get("base_agent")
        target_cli = self.base_agent or self.id
        default_exec = {"codex": "codex", "antigravity": "agy", "deepseek-harness": "dsh"}.get(target_cli, self.id)
        self.executable = data.get("executable", default_exec)
        if target_cli == "deepseek-harness" and self.executable == "dsh":
            from .harness import resolve_executable
            self.executable = resolve_executable()
        self.endpoint = data.get("endpoint", "")
        self.model = data.get("model", "")
        self.api_key_env = data.get("api_key_env", "")
        self.system_prompt = data.get("system_prompt", "")
        self._stored_secret = stored_secret or ""
        self.roles = data.get("roles", ["chat"]) if self.type == "cli" else ["chat", "analysis"]
        if target_cli == "antigravity" and "analysis" not in self.roles:
            self.roles = [*self.roles, "analysis"]
        if target_cli == "codex":
            for r in ("code", "plan", "chat", "analysis", "review", "verify"):
                if r not in self.roles:
                    self.roles.append(r)
        # Adapter-supported capabilities, not a fixed job description. API-only
        # agents can reason about supplied material but cannot run tests or edit.
        extra = {"codex": ["analysis", "review", "verify"],
                 "antigravity": ["plan", "review", "verify", "chat", "analysis"]}.get(target_cli, [])
        if self.type == "openai_compat":
            extra = ["plan", "review"]
        self.roles = list(dict.fromkeys([*self.roles, *extra]))
        self.color = data.get("color", "#6366f1")
        self.avatar = data.get("avatar", "🤖")
        self.description = data.get("description", "")
        self.enabled = data.get("enabled", True)

    @property
    def has_key(self):
        env_val = os.environ.get(self.api_key_env, "") if self.api_key_env else ""
        return bool(env_val or self._stored_secret)

    def resolve_api_key(self):
        if self.api_key_env and self.api_key_env in os.environ:
            env_val = os.environ.get(self.api_key_env, "")
            if env_val:
                return env_val
            # If explicitly patched to "" in environment and no stored secret, return ""
            return self._stored_secret or ""
        if self.api_key_env and os.environ.get(self.api_key_env):
            return os.environ.get(self.api_key_env, "")
        return self._stored_secret or ""

    @property
    def writable(self):
        """Whether this adapter can actually change files when granted the write turn.

        The Antigravity adapter always runs in read-only plan mode and an
        OpenAI-compatible agent has no filesystem access at all, so the platform
        must not hand either of them a write grant it cannot use.
        """
        if not self.enabled:
            return False
        if self.type != "cli":
            return False
        return (self.base_agent or self.id) in ("codex", "deepseek-harness")

    @property
    def ready(self):
        if not self.enabled:
            return False
        if self.type == "cli":
            target_cli = self.base_agent or self.id
            if target_cli == "deepseek-harness":
                return shutil.which(self.executable) is not None
            return target_cli in ("codex", "antigravity")  # Actual executable/login is checked at runtime.
        return local_endpoint(self.endpoint) or self.has_key

    def to_dict(self):
        return {"id": self.id, "name": self.name, "type": self.type,
                "base_agent": self.base_agent,
                "executable": self.executable if self.type == "cli" else None,
                "endpoint": self.endpoint, "model": self.model,
                "api_key_env": self.api_key_env, "roles": self.roles,
                "color": self.color, "avatar": self.avatar,
                "description": self.description,
                "system_prompt": self.system_prompt,
                "enabled": self.enabled,
                "ready": self.ready,
                "writable": self.writable,
                "has_key": self.has_key,
                "has_stored_key": bool(self._stored_secret)}


class AgentRegistry:
    def __init__(self, engine):
        self.engine = engine
        self._secrets_file = Path(self.engine.path).parent / "secrets.json"
        with self.engine.transaction() as db:
            db.execute("CREATE TABLE IF NOT EXISTS removed_agents (id TEXT PRIMARY KEY)")
            for item in DEFAULT_AGENTS:
                if db.execute("SELECT id FROM removed_agents WHERE id=?", (item["id"],)).fetchone():
                    continue
                db.execute("INSERT OR IGNORE INTO agents(id,name,type,body) VALUES(?,?,?,?)",
                           (item["id"], item["name"], item["type"], json.dumps(item, ensure_ascii=False)))
            # Older prototypes accepted plaintext keys. Remove them from live rows;
            # SQLite history/backups may retain old bytes, so operators should rotate.
            for row in db.execute("SELECT id,body FROM agents"):
                data = json.loads(row["body"])
                changed = False
                if "api_key" in data:
                    del data["api_key"]
                    changed = True
                # Only migrate known default labels; preserve user names,
                # endpoints, credentials, enabled state and historical IDs.
                if (data.get("id") == "deepseek-hermes" and data.get("type") == "openai_compat"
                        and data.get("name") in ("DeepSeek Hermes", "DeepSeek（API 模板）")
                        and data.get("endpoint", "").rstrip("/") == "https://api.deepseek.com/v1"
                        and data.get("model") == "deepseek-chat"):
                    data["name"] = "DeepSeek API（云端接口）"
                    data["description"] = "通过 API 分析与讨论；不是本地 Harness，不能直接改文件或运行测试"
                    changed = True
                if (data.get("id") == "deepseek-harness" and data.get("type") == "cli"
                        and data.get("name") == "DeepSeek Harness"):
                    data["name"] = "DeepSeek Harness（本机 CLI）"
                    changed = True
                if changed:
                    db.execute("UPDATE agents SET name=?,body=? WHERE id=?",
                               (data["name"], json.dumps(data, ensure_ascii=False), row["id"]))

    def _load_secrets(self):
        if not self._secrets_file.is_file():
            return {}
        try:
            data = json.loads(self._secrets_file.read_text(encoding="utf-8"))
            return data if isinstance(data, dict) else {}
        except (OSError, ValueError):
            return {}

    def _save_secrets(self, secrets):
        self._secrets_file.parent.mkdir(parents=True, exist_ok=True)
        self._secrets_file.write_text(json.dumps(secrets, ensure_ascii=False, indent=2), encoding="utf-8")
        try:
            os.chmod(self._secrets_file, 0o600)
        except OSError:
            pass

    def set_secret(self, agent_id, secret_value):
        require(isinstance(secret_value, str) and len(secret_value) <= 512, "Invalid API key", "INVALID_INPUT")
        cfg = self.get(agent_id)
        require(cfg is not None, "Unknown agent", "NOT_FOUND")
        secrets = self._load_secrets()
        cleaned = secret_value.strip()
        if cleaned:
            secrets[agent_id] = cleaned
        else:
            secrets.pop(agent_id, None)
        self._save_secrets(secrets)
        return self.get(agent_id).to_dict()

    def list(self, enabled_only=False):
        secrets = self._load_secrets()
        with self.engine.transaction() as db:
            agents = [AgentConfig(json.loads(r[0]), stored_secret=secrets.get(json.loads(r[0])["id"], "")).to_dict()
                      for r in db.execute("SELECT body FROM agents ORDER BY rowid")]
        return [a for a in agents if a["enabled"]] if enabled_only else agents

    def get(self, agent_id):
        secrets = self._load_secrets()
        with self.engine.transaction() as db:
            row = db.execute("SELECT body FROM agents WHERE id=?", (agent_id,)).fetchone()
        return AgentConfig(json.loads(row[0]), stored_secret=secrets.get(agent_id, "")) if row else None

    @staticmethod
    def _ensure_not_in_flight(db, agent_id):
        for row in db.execute("SELECT body FROM messages"):
            message = json.loads(row[0])
            participants = message.get("participants") or (["codex", "antigravity"]
                           if message["recipient"] in ("both", "all") else [message["recipient"]])
            if message["status"] in ("QUEUED", "RUNNING") and agent_id in participants:
                raise CollabError("INVALID_STATE", "Agent has an unfinished conversation")
        for row in db.execute("SELECT body FROM tasks"):
            task = json.loads(row[0])
            if task["phase"] == "EXECUTING" and any(
                    agent_id in (step.get("assignee", "codex"), step.get("verifier", "antigravity")) and step["status"] != "COMPLETED"
                    for step in task["steps"]):
                raise CollabError("INVALID_STATE", "Agent owns unfinished work")
            if any(d["role"] == agent_id and d["status"] in ("ACTIVE", "BLOCKED") for d in task["dispatches"]):
                raise CollabError("INVALID_STATE", "Agent has an unresolved dispatch")
            if task.get("team") and task["status"] != "COMPLETED" and agent_id in task["team"]["members"]:
                raise CollabError("INVALID_STATE", "Agent belongs to an unfinished team task")

    def register(self, data):
        require(isinstance(data, dict), "Agent object required", "INVALID_INPUT")
        require("api_key" not in data, "Set API keys in the server environment, not the database", "INVALID_INPUT")
        agent_id = data.get("id")
        require(isinstance(agent_id, str) and re.fullmatch(r"[a-z][a-z0-9-]{0,63}", agent_id),
                "Agent ID must be lowercase letters, digits or hyphens", "INVALID_INPUT")
        require(agent_id not in ("codex", "antigravity", "deepseek-harness"), "Built-in agents cannot be replaced", "INVALID_INPUT")
        agent_type = data.get("type")
        base_agent = data.get("base_agent")
        is_cloned_cli = agent_type == "cli" and base_agent in ("codex", "antigravity", "deepseek-harness")
        require(agent_type == "openai_compat" or is_cloned_cli,
                "Custom agents currently require an OpenAI-compatible API", "INVALID_INPUT")
        require(type(data.get("enabled", True)) is bool, "enabled must be boolean", "INVALID_INPUT")
        for key in ("description", "avatar", "color"):
            require(isinstance(data.get(key, ""), str) and len(data.get(key, "")) <= 300,
                    "Invalid display field", "INVALID_INPUT")
        sys_prompt = data.get("system_prompt", "")
        require(isinstance(sys_prompt, str) and len(sys_prompt) <= 4000, "Invalid system_prompt", "INVALID_INPUT")

        if is_cloned_cli:
            require(string(data.get("name")) and len(data["name"]) <= 100, "Name required", "INVALID_INPUT")
            default_roles = (
                ["code", "plan", "chat", "analysis", "review", "verify"]
                if base_agent in ("codex", "deepseek-harness")
                else ["review", "verify", "chat", "analysis", "plan"]
            )
            clean = {
                "id": agent_id,
                "name": data["name"].strip(),
                "type": "cli",
                "base_agent": base_agent,
                "executable": {"codex": "codex", "antigravity": "agy", "deepseek-harness": "dsh"}[base_agent],
                "model": (data.get("model") or "").strip(),
                "roles": default_roles,
                "enabled": data.get("enabled", True),
                "color": data.get("color") or ("#6366f1" if base_agent == "codex" else "#10b981"),
                "avatar": data.get("avatar") or ("🤖" if base_agent == "codex" else "🦅"),
                "description": (data.get("description") or "").strip(),
                "system_prompt": sys_prompt.strip(),
            }
        else:
            require(string(data.get("name")) and len(data["name"]) <= 100 and string(data.get("model"))
                    and len(data["model"]) <= 150, "Name and model required", "INVALID_INPUT")
            validate_endpoint(data.get("endpoint"))
            env = data.get("api_key_env", "")
            require(isinstance(env, str) and (not env or re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", env)),
                    "Invalid API key environment variable", "INVALID_INPUT")
            clean = {"id": agent_id, "name": data["name"].strip(), "type": "openai_compat",
                     "endpoint": data["endpoint"].rstrip("/"), "model": data["model"].strip(),
                     "api_key_env": env, "roles": ["chat", "analysis"], "enabled": data.get("enabled", True),
                     "color": data.get("color") or "#6366f1", "avatar": data.get("avatar") or "🤖",
                     "description": data.get("description") or "",
                     "system_prompt": sys_prompt.strip()}
        with self.engine.transaction() as db:
            if db.execute("SELECT id FROM agents WHERE id=?", (agent_id,)).fetchone():
                self._ensure_not_in_flight(db, agent_id)
            db.execute("INSERT OR REPLACE INTO agents VALUES(?,?,?,?)",
                       (agent_id, clean["name"], clean["type"], json.dumps(clean, ensure_ascii=False)))
            db.execute("DELETE FROM removed_agents WHERE id=?", (agent_id,))
        if isinstance(data.get("secret_key"), str) and data.get("secret_key").strip():
            self.set_secret(agent_id, data["secret_key"].strip())
        secrets = self._load_secrets()
        return AgentConfig(clean, stored_secret=secrets.get(agent_id, "")).to_dict()

    def update_profile(self, agent_id, patch_data):
        require(isinstance(patch_data, dict), "Patch object required", "INVALID_INPUT")
        with self.engine.transaction() as db:
            row = db.execute("SELECT body FROM agents WHERE id=?", (agent_id,)).fetchone()
            require(row is not None, "Unknown agent", "NOT_FOUND")
            data = json.loads(row[0])
            if "name" in patch_data and string(patch_data["name"]):
                require(len(patch_data["name"]) <= 100, "Name too long", "INVALID_INPUT")
                data["name"] = patch_data["name"].strip()
            if "description" in patch_data and isinstance(patch_data["description"], str):
                require(len(patch_data["description"]) <= 300, "Description too long", "INVALID_INPUT")
                data["description"] = patch_data["description"].strip()
            if "system_prompt" in patch_data and isinstance(patch_data["system_prompt"], str):
                require(len(patch_data["system_prompt"]) <= 4000, "System prompt too long", "INVALID_INPUT")
                data["system_prompt"] = patch_data["system_prompt"].strip()
            if "color" in patch_data and string(patch_data["color"]):
                data["color"] = patch_data["color"].strip()
            if "avatar" in patch_data and string(patch_data["avatar"]):
                data["avatar"] = patch_data["avatar"].strip()
            if data.get("type") == "openai_compat":
                if "model" in patch_data and string(patch_data["model"]):
                    data["model"] = patch_data["model"].strip()
                if "endpoint" in patch_data and string(patch_data["endpoint"]):
                    validate_endpoint(patch_data["endpoint"])
                    data["endpoint"] = patch_data["endpoint"].rstrip("/")
            db.execute("UPDATE agents SET name=?,body=? WHERE id=?",
                       (data["name"], json.dumps(data, ensure_ascii=False), agent_id))
        if "secret_key" in patch_data and isinstance(patch_data["secret_key"], str):
            self.set_secret(agent_id, patch_data["secret_key"])
        return self.get(agent_id).to_dict()

    def remove(self, agent_id, cancel_queued=False):
        require(agent_id not in ("codex", "antigravity", "deepseek-harness"), "Built-in agents cannot be removed", "INVALID_INPUT")
        require(type(cancel_queued) is bool, "cancel_queued must be boolean", "INVALID_INPUT")
        with self.engine.transaction() as db:
            if cancel_queued:
                # Explicit removal may cancel unsent, single-recipient messages.
                # Never discard shared conversations, in-flight work or history.
                for row in db.execute("SELECT body FROM messages").fetchall():
                    message = json.loads(row[0])
                    if (message["status"] == "QUEUED" and not message["replies"]
                            and message["recipient"] == agent_id
                            and self.engine._chat_roles(message) == [agent_id]):
                        message.update(status="CANCELLED", error="接收 Agent 已移除，此消息未发送；历史记录已保留。")
                        db.execute("UPDATE messages SET body=? WHERE task_id=? AND id=?",
                                   (encode(message), message["task_id"], message["id"]))
                        task = self.engine._task(db, message["task_id"])
                        self.engine._save(db, task, "MESSAGE_CANCELLED", {
                            "message_id": message["id"], "agent_id": agent_id, "reason": "AGENT_REMOVED"})
            self._ensure_not_in_flight(db, agent_id)
            removed = db.execute("DELETE FROM agents WHERE id=?", (agent_id,)).rowcount > 0
            if removed:
                db.execute("INSERT OR IGNORE INTO removed_agents VALUES(?)", (agent_id,))
        secrets = self._load_secrets()
        if agent_id in secrets:
            secrets.pop(agent_id, None)
            self._save_secrets(secrets)
        return removed

    def set_enabled(self, agent_id, enabled):
        require(type(enabled) is bool, "enabled must be boolean", "INVALID_INPUT")
        with self.engine.transaction() as db:
            row = db.execute("SELECT body FROM agents WHERE id=?", (agent_id,)).fetchone()
            require(row is not None, "Unknown agent", "NOT_FOUND")
            data = json.loads(row[0])
            if data.get("enabled", True) != enabled:
                self._ensure_not_in_flight(db, agent_id)
                data["enabled"] = enabled
                db.execute("UPDATE agents SET body=? WHERE id=?", (json.dumps(data, ensure_ascii=False), agent_id))
        return self.get(agent_id).to_dict()

    def get_runtime(self, agent_id, custom_executables=None):
        from .runtime import Runtime
        from .antigravity import AntigravityRuntime
        from .openai_runtime import OpenAICompatibleRuntime
        from .server import resolve_antigravity_executable, resolve_codex_executable
        cfg = self.get(agent_id)
        require(cfg is not None and cfg.enabled, "Agent is not enabled", "UNSUPPORTED_PROVIDER")
        target_cli = cfg.base_agent or cfg.id
        if cfg.type == "cli":
            if target_cli == "codex":
                exe = (custom_executables or {}).get(agent_id) or (custom_executables or {}).get("codex") or cfg.executable
                rt = Runtime(self.engine, executable=resolve_codex_executable(exe))
                rt.role = cfg.id
                rt.system_prompt = cfg.system_prompt
                return rt
            if target_cli == "antigravity":
                exe = (custom_executables or {}).get(agent_id) or (custom_executables or {}).get("antigravity") or resolve_antigravity_executable(cfg.executable)
                rt = AntigravityRuntime(self.engine, executable=exe, model=cfg.model or None)
                rt.role = cfg.id
                rt.system_prompt = cfg.system_prompt
                return rt
            if target_cli == "deepseek-harness":
                from .harness import HarnessRuntime
                require(cfg.ready, "Harness executable not found", "UNSUPPORTED_PROVIDER")
                exe = (custom_executables or {}).get(agent_id) or (custom_executables or {}).get("deepseek-harness") or cfg.executable
                rt = HarnessRuntime(self.engine, executable=exe)
                rt.role = cfg.id
                rt.system_prompt = cfg.system_prompt
                return rt
        require(cfg.type == "openai_compat" and cfg.ready, "Agent is not configured", "UNSUPPORTED_PROVIDER")
        return OpenAICompatibleRuntime(self.engine, cfg)
