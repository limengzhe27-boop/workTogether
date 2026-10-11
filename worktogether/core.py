"""Transactional, provider-independent task state machine. See docs/contracts/v1.md."""
import json
import hashlib
import shutil
import sqlite3
import subprocess
import time
import uuid
from contextlib import contextmanager
from pathlib import Path


class CollabError(Exception):
    def __init__(self, code, message):
        super().__init__(message)
        self.code = code


def require(condition, message, code="INVALID_REPORT"):
    if not condition:
        raise CollabError(code, message)


def string(value):
    return isinstance(value, str) and bool(value.strip())


def strings(value, nonempty=False):
    return isinstance(value, list) and (bool(value) or not nonempty) and all(string(x) for x in value)


def exact(value, fields):
    require(isinstance(value, dict) and set(value) == set(fields), "Unexpected or missing fields: " + ", ".join(fields))


def encode(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, allow_nan=False)


def uid():
    return uuid.uuid4().hex


def revision_dispatches(task):
    return [d for d in task["dispatches"]
            if d["payload"].get("requirement_revision", 1) == task.get("revision", 1)]


def plan_validate(data):
    exact(data, ["steps"])
    steps = data["steps"]
    require(isinstance(steps, list) and 0 < len(steps) <= 100, "Plan needs 1–100 steps", "INVALID_PLAN")
    by_id = {}
    for step in steps:
        require(isinstance(step, dict) and set(step).issubset({"id", "title", "acceptance", "depends_on", "assignee", "work_kind", "verifier", "assignment_reason"})
                and {"id", "title", "acceptance", "depends_on"}.issubset(set(step)),
                "Invalid step fields", "INVALID_PLAN")
        require(string(step["id"]) and string(step["title"]) and strings(step["acceptance"], True)
                and strings(step["depends_on"]), "Invalid step fields", "INVALID_PLAN")
        if "assignee" in step:
            require(string(step["assignee"]), "Invalid assignee", "INVALID_PLAN")
        for field in ("verifier", "assignment_reason"):
            if field in step:
                require(string(step[field]) and len(step[field]) <= 2000, "Invalid " + field, "INVALID_PLAN")
        require(step.get("work_kind", "code") in ("code", "analysis"), "Unknown work kind", "INVALID_PLAN")
        require(step["id"] not in by_id and len(set(step["acceptance"])) == len(step["acceptance"])
                and len(set(step["depends_on"])) == len(step["depends_on"]), "Duplicate step or criterion", "INVALID_PLAN")
        by_id[step["id"]] = step
    visited, visiting = set(), set()

    def visit(key):
        require(key in by_id, "Unknown dependency", "INVALID_PLAN")
        require(key not in visiting, "Cyclic dependency", "INVALID_PLAN")
        if key in visited:
            return
        visiting.add(key)
        for dep in by_id[key]["depends_on"]:
            visit(dep)
        visiting.remove(key)
        visited.add(key)

    for key in by_id:
        visit(key)
    return steps


class Engine:
    def __init__(self, db_path):
        self.path = str(Path(db_path).expanduser().resolve())
        Path(self.path).parent.mkdir(parents=True, exist_ok=True)
        with self.transaction() as db:
            db.executescript("""
                CREATE TABLE IF NOT EXISTS projects (
                    id TEXT PRIMARY KEY, name TEXT NOT NULL, path TEXT NOT NULL UNIQUE);
                CREATE TABLE IF NOT EXISTS project_meta (
                    project_id TEXT PRIMARY KEY REFERENCES projects(id),
                    memory TEXT NOT NULL DEFAULT '',
                    updated_at REAL NOT NULL DEFAULT 0);
                CREATE TABLE IF NOT EXISTS tasks (
                    id TEXT PRIMARY KEY, project_id TEXT NOT NULL REFERENCES projects(id),
                    body TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS events (
                    seq INTEGER PRIMARY KEY AUTOINCREMENT, task_id TEXT NOT NULL REFERENCES tasks(id),
                    created_at REAL NOT NULL, kind TEXT NOT NULL, body TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS messages (
                    id TEXT NOT NULL, task_id TEXT NOT NULL REFERENCES tasks(id), body TEXT NOT NULL,
                    PRIMARY KEY(task_id,id));
                CREATE TABLE IF NOT EXISTS agents (
                    id TEXT PRIMARY KEY, name TEXT NOT NULL, type TEXT NOT NULL, body TEXT NOT NULL);
            """)

    @contextmanager
    def transaction(self):
        db = sqlite3.connect(self.path, timeout=10, isolation_level=None)
        db.row_factory = sqlite3.Row
        db.execute("PRAGMA foreign_keys=ON")
        try:
            db.execute("BEGIN IMMEDIATE")
            yield db
            db.commit()
        except BaseException:
            db.rollback()
            raise
        finally:
            db.close()

    def _project(self, db, project):
        row = db.execute("SELECT * FROM projects WHERE id=?", (project,)).fetchone()
        require(row is not None, "Unknown project", "NOT_FOUND")
        p = dict(row)
        meta = db.execute("SELECT memory FROM project_meta WHERE project_id=?", (project,)).fetchone()
        if meta and meta["memory"]:
            p["memory"] = meta["memory"]
        return p

    def _task(self, db, task):
        row = db.execute("SELECT body FROM tasks WHERE id=?", (task,)).fetchone()
        require(row is not None, "Unknown task", "NOT_FOUND")
        t = json.loads(row["body"])
        # Missing permission is not consent. Preserve an explicitly saved value.
        config = t.setdefault("config", {})
        config.setdefault("allow_write", False)
        config.setdefault("max_auto_rounds", 2)
        config.setdefault("write_epoch", 0)
        return t

    def _save(self, db, task, kind, detail):
        task["updated_at"] = time.time()
        db.execute("UPDATE tasks SET body=? WHERE id=?", (encode(task), task["id"]))
        db.execute("INSERT INTO events(task_id,created_at,kind,body) VALUES(?,?,?,?)",
                   (task["id"], task["updated_at"], kind, encode(detail)))

    def project_add(self, name, path):
        require(string(name), "Project name required", "INVALID_INPUT")
        resolved = Path(path).expanduser().resolve()
        require(resolved.is_dir(), "Project directory does not exist", "INVALID_INPUT")
        with self.transaction() as db:
            existing = db.execute("SELECT * FROM projects WHERE path=?", (str(resolved),)).fetchone()
            if existing:
                return dict(existing)
            project = {"id": uid(), "name": name, "path": str(resolved)}
            db.execute("INSERT INTO projects VALUES(:id,:name,:path)", project)
            return project

    def project_memory_update(self, project_id, memory):
        require(isinstance(memory, str) and len(memory) <= 20000, "Invalid project memory", "INVALID_INPUT")
        with self.transaction() as db:
            p = self._project(db, project_id)
            clean = memory.strip()
            db.execute("INSERT OR REPLACE INTO project_meta(project_id,memory,updated_at) VALUES(?,?,?)",
                       (project_id, clean, time.time()))
            p["memory"] = clean
            return p

    def _delete_task_rows(self, db, task_id):
        if db.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='runs'").fetchone():
            for r in db.execute("SELECT body FROM runs WHERE task_id=?", (task_id,)):
                require(json.loads(r[0])["status"] not in ("PREPARED", "RUNNING"),
                        "Cannot delete a session while an agent process is running", "INVALID_STATE")
            db.execute("DELETE FROM runs WHERE task_id=?", (task_id,))
        for tbl in ("sessions", "approvals", "planning_jobs", "execution_jobs", "team_jobs", "messages", "events"):
            if db.execute("SELECT name FROM sqlite_master WHERE type='table' AND name=?", (tbl,)).fetchone():
                db.execute(f"DELETE FROM {tbl} WHERE task_id=?", (task_id,))
        db.execute("DELETE FROM tasks WHERE id=?", (task_id,))

    def project_delete(self, project_id):
        with self.transaction() as db:
            self._project(db, project_id)
            task_ids = [r[0] for r in db.execute("SELECT id FROM tasks WHERE project_id=?", (project_id,)).fetchall()]
            for tid in task_ids:
                self._delete_task_rows(db, tid)
            db.execute("DELETE FROM project_meta WHERE project_id=?", (project_id,))
            db.execute("DELETE FROM projects WHERE id=?", (project_id,))
            return {"deleted": project_id, "tasks_removed": len(task_ids)}

    def task_add(self, project, title, requirement):
        require(string(title) and string(requirement), "Title and requirement required", "INVALID_INPUT")
        with self.transaction() as db:
            self._project(db, project)
            task = {"id": uid(), "project_id": project, "title": title, "requirement": requirement,
                    "revision": 1, "revisions": [],
                    "status": "ACTIVE", "phase": "PLANNING", "block_reason": None,
                    "config": {"max_repairs": 2, "timeout_seconds": 1800,
                               # Choosing recipients is not a write grant. The user enables
                               # writes explicitly; grants remain serialized per project.
                               "allow_write": False, "max_auto_rounds": 2, "write_epoch": 0},
                    "draft_plan": None, "plan_review": None, "steps": [], "dispatches": [],
                    "auto_rounds_used": 0, "created_at": time.time()}
            db.execute("INSERT INTO tasks VALUES(?,?,?)", (task["id"], project, encode(task)))
            self._save(db, task, "TASK_CREATED", {"title": title})
            return task

    def task_rename(self, task_id, title):
        require(string(title) and len(title.strip()) <= 200, "Valid session title required", "INVALID_INPUT")
        with self.transaction() as db:
            task = self._task(db, task_id)
            task["title"] = title.strip()
            self._save(db, task, "TASK_RENAMED", {"title": task["title"]})
            return task

    def task_delete(self, task_id):
        with self.transaction() as db:
            self._task(db, task_id)
            self._delete_task_rows(db, task_id)
            return {"deleted": task_id}

    def revise(self, task_id, message_id, expected_revision, requirement):
        """Replace requirements only at an idle boundary; retain the full audit trail."""
        require(string(message_id) and len(message_id) <= 128 and string(requirement)
                and len(requirement) <= 100000 and type(expected_revision) is int
                and expected_revision >= 1, "Invalid revision fields", "INVALID_INPUT")
        with self.transaction() as db:
            task = self._task(db, task_id)
            history = task.setdefault("revisions", [])
            for entry in history:
                if entry["message_id"] == message_id:
                    require(entry["expected_revision"] == expected_revision and
                            entry["new_requirement"] == requirement,
                            "Message ID already used with different content", "CONFLICT")
                    return {"revision": entry["revision"], "duplicate": True}
            revision = task.get("revision", 1)
            require(revision == expected_revision, "Task changed; refresh before revising", "CONFLICT")
            require(not any(d["status"] == "ACTIVE" for d in task["dispatches"]),
                    "Finish or resolve all in-flight dispatches before revising", "INVALID_STATE")
            for table, states in (("runs", ("PREPARED", "RUNNING", "EXITED")),
                                  ("planning_jobs", ("QUEUED", "RUNNING")),
                                  ("team_jobs", ("QUEUED", "RUNNING")),
                                  ("execution_jobs", ("QUEUED", "RUNNING"))):
                if db.execute("SELECT name FROM sqlite_master WHERE type='table' AND name=?", (table,)).fetchone():
                    for row in db.execute(f"SELECT body FROM {table} WHERE task_id=?", (task_id,)):
                        require(json.loads(row[0])["status"] not in states,
                                "Running or unresolved work prevents revision", "INVALID_STATE")
            history.append({"message_id": message_id, "expected_revision": revision,
                            "revision": revision + 1, "new_requirement": requirement,
                            "previous_requirement": task["requirement"],
                            "previous_plan": task["draft_plan"], "previous_steps": task["steps"],
                            "created_at": time.time()})
            for d in task["dispatches"]:
                if d["status"] == "BLOCKED":
                    d["status"] = "SUPERSEDED"
            for row in db.execute("SELECT id,body FROM messages WHERE task_id=?", (task_id,)):
                message = json.loads(row["body"])
                if message["status"] in ("QUEUED", "BLOCKED"):
                    message["status"] = "NEEDS_REVIEW"
                    db.execute("UPDATE messages SET body=? WHERE task_id=? AND id=?",
                               (encode(message), task_id, row["id"]))
            task.update(requirement=requirement, revision=revision + 1, draft_plan=None,
                        plan_review=None, steps=[], phase="PLANNING", block_reason=None,
                        status="PAUSED" if task["status"] == "PAUSED" else "ACTIVE")
            if db.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='approvals'").fetchone():
                db.execute("DELETE FROM approvals WHERE task_id=?", (task_id,))
            self._save(db, task, "REQUIREMENT_REVISED", {"message_id": message_id,
                       "revision": revision + 1, "requirement": requirement})
            return {"revision": revision + 1, "duplicate": False}

    def configure_team(self, task_id, members, coordinator=None, reviewer=None):
        """Freeze task participants before planning; never change running work."""
        from .agents import AgentRegistry, AgentConfig
        AgentRegistry(self)
        require(strings(members, True) and 2 <= len(members) <= 12
                and len(members) == len(set(members)), "Choose 2–12 unique agents", "INVALID_INPUT")
        with self.transaction() as db:
            task = self._task(db, task_id)
            require(task["status"] == "ACTIVE" and task["phase"] == "PLANNING"
                    and not revision_dispatches(task), "Configure the team before this revision's dispatches", "INVALID_STATE")
            for table in ("planning_jobs", "team_jobs"):
                if db.execute("SELECT name FROM sqlite_master WHERE name=?", (table,)).fetchone():
                    require(not any(json.loads(row[0])["status"] in ("QUEUED", "RUNNING") for row in db.execute(
                        f"SELECT body FROM {table} WHERE task_id=?", (task_id,))),
                        "An active workflow prevents changing the team", "INVALID_STATE")
            require(not any(json.loads(row[0])["status"] in ("QUEUED", "RUNNING") for row in db.execute(
                "SELECT body FROM messages WHERE task_id=?", (task_id,))),
                "Finish queued conversations before changing the team", "INVALID_STATE")
            agents = {a.id: a for a in (AgentConfig(json.loads(row[0])) for row in db.execute("SELECT body FROM agents"))}
            require(all(key in agents and agents[key].ready for key in members),
                    "Team contains an unavailable agent", "INVALID_INPUT")
            executable_kinds = [kind for kind in ("code", "analysis")
                                if any(kind in agents[owner].roles and any(
                                    checker != owner and "verify" in agents[checker].roles for checker in members)
                                    for owner in members)]
            require(bool(executable_kinds), "Team needs a capable worker and an independent verifier", "INVALID_INPUT")
            planners = sorted(key for key in members if "plan" in agents[key].roles)
            require(bool(planners), "Team needs a planning-capable agent", "INVALID_INPUT")
            coordinator = coordinator or planners[0]
            require(coordinator in planners, "Coordinator cannot plan", "INVALID_INPUT")
            reviewers = sorted(key for key in members if key != coordinator and "review" in agents[key].roles)
            require(bool(reviewers), "Team needs an independent plan reviewer", "INVALID_INPUT")
            reviewer = reviewer or reviewers[0]
            require(reviewer in reviewers, "Invalid independent reviewer", "INVALID_INPUT")
            task["team"] = {"members": members, "coordinator": coordinator, "reviewer": reviewer}
            self._save(db, task, "TEAM_CONFIGURED", task["team"])
            return task["team"]

    def team_round(self, task_id, kind):
        """Atomically issue one finite read-only round, never replay partial work."""
        require(kind in ("CONSULT", "CLAIM"), "Unknown team round", "INVALID_INPUT")
        from .agents import AgentConfig
        with self.transaction() as db:
            task = self._task(db, task_id)
            require(task.get("team") and task["status"] == "ACTIVE"
                    and task["phase"] == ("PLANNING" if kind == "CONSULT" else "PLAN_REVIEW"),
                    "Task is not ready for this team round", "INVALID_STATE")
            previous = [d for d in task["dispatches"] if d["kind"] == kind
                        and d["payload"]["requirement_revision"] == task.get("revision", 1)]
            require(not previous, "Round already issued; inspect existing results", "ALREADY_STARTED")
            require(not any(d["status"] == "ACTIVE" for d in task["dispatches"]),
                    "Existing dispatch must finish", "INVALID_STATE")
            require(not any(json.loads(row[0])["status"] in ("QUEUED", "RUNNING") for row in db.execute(
                "SELECT body FROM messages WHERE task_id=?", (task_id,))), "Conversation pending", "INVALID_STATE")
            agents = {a.id: a for a in (AgentConfig(json.loads(row[0])) for row in db.execute("SELECT body FROM agents"))}
            members = task["team"]["members"]
            require(all(key in agents and agents[key].ready and not self._busy(db, task, key) for key in members),
                    "A team member is unavailable or busy", "INVALID_STATE")
            dispatched = []
            for role in members:
                d = self._issue(task, kind, role=role)
                d["payload"]["capabilities"] = agents[role].roles
                dispatched.append(d)
            self._save(db, task, "TEAM_ROUND_STARTED", {"kind": kind, "dispatch_ids": [d["id"] for d in dispatched]})
            return dispatched

    def projects_list(self):
        with self.transaction() as db:
            projects = [dict(r) for r in db.execute("SELECT * FROM projects ORDER BY name")]
            for p in projects:
                count = db.execute("SELECT COUNT(*) as c FROM tasks WHERE project_id=?", (p["id"],)).fetchone()["c"]
                p["task_count"] = count
                meta = db.execute("SELECT memory FROM project_meta WHERE project_id=?", (p["id"],)).fetchone()
                p["memory"] = meta["memory"] if meta and meta["memory"] else ""
            return projects

    def messages(self, task_id):
        return self._chat_view(task_id)[0]

    def chat_state(self, task_id):
        """Session-level group chat state: write grant, round budget, held messages."""
        return self._chat_view(task_id)[1]

    def _chat_view(self, task_id):
        with self.transaction() as db:
            task = self._task(db, task_id)
            messages = [json.loads(r[0]) for r in db.execute("SELECT body FROM messages WHERE task_id=? ORDER BY rowid", (task_id,))]
            queued = False
            config = task.get("config", {})
            for message in messages:
                roles = self._chat_roles(message)
                round_no = message.get("round", 1)
                _, _, answered, failed_roles, pending = self._round_state(task, message)
                message.setdefault("sender", "user")
                message["write_authorized"] = self._message_can_write(task, message)
                message["pending_roles"] = pending
                message["failed_roles"] = sorted(failed_roles)
                if message["status"] == "QUEUED":
                    message["wait_reason"] = (self._chat_wait_reason(db, task) or
                        ("EARLIER_MESSAGE" if queued else self._chat_member_wait_reason(db, task, message)))
                    queued = True
                elif message["status"] == "RUNNING":
                    active = [d for d in task["dispatches"]
                              if d["kind"] == "CHAT" and d["status"] == "ACTIVE"
                              and d["payload"].get("message_id") == message["id"]]
                    if active:
                        message["active_roles"] = [d["role"] for d in active]
                        message["active_since"] = min(d["created_at"] for d in active)
                        message["write_role"] = next(
                            (d["role"] for d in active if d["payload"].get("allow_write")), None)
                elif message["status"] == "BLOCKED":
                    blocked = [d for d in task["dispatches"]
                               if d["kind"] == "CHAT" and d["status"] == "BLOCKED"
                               and d["payload"].get("message_id") == message["id"]]
                    if blocked:
                        message["blocked_role"] = blocked[-1]["role"]
                        message["blocked_dispatch_id"] = blocked[-1]["id"]
                        message["blocked_roles"] = [d["role"] for d in blocked]
                elif message["status"] == "HELD":
                    message["wait_reason"] = "AUTO_ROUND_BUDGET"
            held = sum(1 for m in messages if m["status"] == "HELD")
            by_dispatch = {d["id"]: d for d in task["dispatches"]}
            for message in messages:
                for reply in message.get("replies", []):
                    dispatch = by_dispatch.get(reply.get("dispatch_id"))
                    if dispatch and dispatch.get("changed_files"):
                        reply["changed_files"] = dispatch["changed_files"]
            return messages, {
                "allow_write": config.get("allow_write", False),
                "max_auto_rounds": config.get("max_auto_rounds", 2),
                "auto_rounds_used": task.get("auto_rounds_used", 0),
                "held": held,
                "available_members": self._available_room_members(db, task),
            }

    def _chat_wait_reason(self, db, task):
        if task["status"] not in ("ACTIVE", "COMPLETED"):
            return "TASK_PAUSED" if task["status"] == "PAUSED" else "TASK_BLOCKED"
        if any(d["status"] == "ACTIVE" for d in task["dispatches"]):
            return "DISPATCH_UNRESOLVED"
        for table in ("planning_jobs", "team_jobs"):
            if db.execute("SELECT name FROM sqlite_master WHERE name=?", (table,)).fetchone():
                if any(json.loads(r[0])["status"] in ("QUEUED", "RUNNING") for r in db.execute(
                        f"SELECT body FROM {table} WHERE task_id=?", (task["id"],))):
                    return "WORKFLOW_RUNNING"
        if db.execute("SELECT name FROM sqlite_master WHERE name='runs'").fetchone():
            if any(json.loads(r[0])["status"] in ("PREPARED", "RUNNING", "EXITED") for r in db.execute(
                    "SELECT body FROM runs WHERE task_id=?", (task["id"],))):
                return "RUN_UNRESOLVED"
        return None

    @staticmethod
    def _chat_roles(message):
        if "participants" in message:
            return message["participants"]
        return (["codex", "antigravity"]
            if message["recipient"] in ("both", "all") else [message["recipient"]])

    def _chat_member_wait_reason(self, db, task, message, role=None):
        if message["revision"] != task.get("revision", 1):
            return "REQUIREMENT_CHANGED"
        roles = self._chat_roles(message)
        round_no = message.get("round", 1)
        pending = [r for r in roles if r not in self._answered_roles(message, round_no)]
        if role is None:
            role = pending[0] if pending else None
        if role is None:
            return None
        from .agents import AgentConfig
        row = db.execute("SELECT body FROM agents WHERE id=?", (role,)).fetchone()
        secrets_file = Path(self.path).parent / "secrets.json"
        stored_secret = ""
        if secrets_file.is_file():
            try:
                stored_secret = json.loads(secrets_file.read_text(encoding="utf-8")).get(role, "")
            except Exception:
                stored_secret = ""
        if row is None or not AgentConfig(json.loads(row[0]), stored_secret=stored_secret).ready:
            return "AGENT_UNAVAILABLE"
        if role not in self._room_members(db, task, message):
            return "ROOM_SCOPE_CHANGED"
        if self._busy(db, task, role):
            return "MEMBER_UNRESOLVED"
        return None

    @staticmethod
    def _answered_roles(message, round_no):
        return {r["role"] for r in message.get("replies", []) if r.get("round", 1) == round_no}

    @staticmethod
    def _round_replies(message, round_no):
        return [r for r in message.get("replies", []) if r.get("round", 1) == round_no]

    def _ready_agent_ids(self, db):
        """Agent ids that are enabled and configured, computed without a nested transaction."""
        from .agents import AgentConfig
        secrets = {}
        secrets_file = Path(self.path).parent / "secrets.json"
        if secrets_file.is_file():
            try:
                secrets = json.loads(secrets_file.read_text(encoding="utf-8"))
            except (OSError, ValueError):
                secrets = {}
        ready = set()
        for row in db.execute("SELECT id,body FROM agents"):
            try:
                cfg = AgentConfig(json.loads(row["body"]), stored_secret=secrets.get(row["id"], ""))
            except (ValueError, KeyError):
                continue
            if cfg.enabled and cfg.ready:
                ready.add(row["id"])
        return ready

    def _available_room_members(self, db, task):
        ready = self._ready_agent_ids(db)
        if task.get("team"):
            return sorted(ready & set(task["team"]["members"]))
        return sorted(ready)

    def _room_members(self, db, task, message):
        # A root user message freezes the selected recipients for its whole chain.
        # Old user messages can safely fall back to their recorded recipients;
        # unscoped legacy automatic messages need human review, not a wider room.
        scope = message.get("room_members")
        if scope is None:
            scope = [] if message.get("auto") else self._chat_roles(message)
        return sorted(set(scope) & set(self._available_room_members(db, task)))

    @staticmethod
    def _message_can_write(task, message):
        config = task.get("config", {})
        return (config.get("allow_write") is True and message.get("allow_write") is True
                and message.get("write_epoch", 0) == config.get("write_epoch", 0))

    def validate_chat_launch(self, db, task, dispatch):
        """Recheck grants under the same transaction as process launch."""
        if dispatch["kind"] != "CHAT":
            return
        row = db.execute("SELECT body FROM messages WHERE task_id=? AND id=?",
                         (task["id"], dispatch["payload"].get("message_id"))).fetchone()
        require(row is not None, "Chat message no longer exists", "ROOM_SCOPE_CHANGED")
        message = json.loads(row[0])
        require(dispatch["role"] in self._room_members(db, task, message),
                "Room membership changed; review or skip this message", "ROOM_SCOPE_CHANGED")
        if dispatch["payload"].get("allow_write"):
            require(self._message_can_write(task, message)
                    and dispatch["payload"].get("write_epoch", 0) == task["config"].get("write_epoch", 0),
                    "Write permission was revoked; skip or explicitly retry read-only, or send a new authorized message",
                    "PERMISSION_REVOKED")
            require(self._project_write_owner(db, task["project_id"], exclude_dispatch=dispatch["id"]) is None,
                    "Another unresolved writer or verifier owns this project", "INVALID_STATE")

    def _round_state(self, task, message):
        """Who still owes this round an answer, and who already failed it.

        A role is settled when it produced a usable reply or when its dispatch
        for this round ended BLOCKED. A reply that came back with status BLOCKED
        counts as a failure even though text was recorded: an agent saying "I
        cannot do this" must not be rendered as a normal answer.
        A failed role is deliberately not pending: an interrupted agent must be
        retried explicitly, never replayed.
        """
        roles = self._chat_roles(message)
        round_no = message.get("round", 1)
        replies = self._round_replies(message, round_no)
        answered = {r["role"] for r in replies if r.get("ok", True)}
        recorded = {r["role"] for r in replies}
        failed = {r["role"] for r in replies if not r.get("ok", True)}
        for d in task["dispatches"]:
            if d["kind"] != "CHAT" or d["payload"].get("message_id") != message["id"]:
                continue
            if d["payload"].get("round", 1) != round_no:
                continue
            if d["status"] == "BLOCKED" and d["role"] not in recorded:
                failed.add(d["role"])
        pending = [role for role in roles if role not in answered and role not in failed]
        return roles, round_no, answered, failed, pending

    def _record_agent_messages(self, db, task, message, dispatch, report):
        """Turn an agent's `data.messages` into real group messages.

        This is what makes the room a room: agents can question, hand work to,
        or claim work from each other without the user relaying anything. The
        automatic back-and-forth is bounded by config.max_auto_rounds; beyond
        that the messages are held until the user asks to continue.
        """
        entries = (report.get("data") or {}).get("messages")
        if not isinstance(entries, list) or not entries:
            return
        members = task.get("team", {}).get("members") if task.get("team") else None
        room = set(self._room_members(db, task, message)) & set(dispatch["payload"].get("room_members", []))
        sender = dispatch["role"]
        budget = task.setdefault("config", {}).setdefault("max_auto_rounds", 2)
        used = task.get("auto_rounds_used", 0)
        for entry in entries[:3]:
            if not isinstance(entry, dict):
                continue
            to = entry.get("to")
            content = entry.get("content")
            intent = entry.get("intent") or "ask"
            if intent not in ("ask", "assign", "claim", "report"):
                continue
            if not (string(to) and string(content) and len(content) <= 4000):
                continue
            if to != "all" and to not in room:
                continue
            if intent in ("ask", "assign") and (to in (sender, "all") or to not in room):
                continue
            if members and to not in ("all", sender) and to not in members:
                continue
            informative = intent in ("claim", "report")
            targets = [] if informative else [to]
            auto = {"id": uid(), "task_id": task["id"], "sender": sender, "intent": intent,
                    "recipient": to, "content": content, "participants": targets,
                    "revision": task.get("revision", 1), "round": 1, "rounds": 1,
                    "replies": [], "created_at": time.time(), "auto": True,
                    "parent_message_id": message["id"]}
            auto["room_members"] = sorted(room)
            auto["write_epoch"] = message.get("write_epoch", 0)
            # Delegation may narrow, never enlarge, the user's original grant.
            if self._message_can_write(task, message):
                auto["allow_write"] = True
            if targets and used >= budget:
                auto["status"] = "HELD"
                auto["held"] = True
            else:
                auto["status"] = "QUEUED" if targets else "DELIVERED"
                if targets:
                    used += 1
            db.execute("INSERT INTO messages VALUES(?,?,?)", (auto["id"], task["id"], encode(auto)))
        task["auto_rounds_used"] = used

    def _project_write_owner(self, db, project_id, exclude_dispatch=None, exclude_task=None):
        """Role currently allowed to change files in this project, or None.

        Group chat keeps discussion parallel but writes serial, so at most one
        chat dispatch may hold the write sandbox for a project at any moment.
        CODE/FIX/VERIFY mutual exclusion is still enforced by _workspace_busy.
        """
        for row in db.execute("SELECT body FROM tasks WHERE project_id=?", (project_id,)):
            other = json.loads(row[0])
            if other["id"] == exclude_task:
                continue
            for d in other["dispatches"]:
                if d["status"] not in ("ACTIVE", "BLOCKED") or d["id"] == exclude_dispatch:
                    continue
                if d["kind"] in ("CODE", "FIX", "VERIFY"):
                    return d["role"]
                if d["kind"] == "CHAT" and d["payload"].get("allow_write"):
                    return d["role"]
        # A writer inside the current task also blocks the next one.
        if exclude_task is not None:
            row = db.execute("SELECT body FROM tasks WHERE id=?", (exclude_task,)).fetchone()
            if row:
                for d in json.loads(row[0])["dispatches"]:
                    if d["status"] in ("ACTIVE", "BLOCKED") and d["id"] != exclude_dispatch \
                            and (d["kind"] in ("CODE", "FIX", "VERIFY")
                                 or (d["kind"] == "CHAT" and d["payload"].get("allow_write"))):
                        return d["role"]
        return None

    def _pick_writer(self, db, task, message, pending):
        """At most one agent per round may actually change files.

        Among several candidates a capable coder is preferred, so a discussion
        round writes through the coding agent. But when the round is addressed to
        a single writable agent - an explicit `assign` hand-off - that agent gets
        the turn even if it is not the team's coder. Only an adapter that can
        really write is ever offered the grant.
        """
        if not self._message_can_write(task, message):
            return None
        if self._project_write_owner(db, task["project_id"], exclude_task=task["id"]):
            return None
        from .agents import AgentConfig
        secrets_file = Path(self.path).parent / "secrets.json"
        stored = {}
        if secrets_file.is_file():
            try:
                stored = json.loads(secrets_file.read_text(encoding="utf-8"))
            except (OSError, ValueError):
                stored = {}
        candidates = []
        for role in pending:
            row = db.execute("SELECT body FROM agents WHERE id=?", (role,)).fetchone()
            if row is None:
                continue
            config = AgentConfig(json.loads(row[0]), stored_secret=stored.get(role, ""))
            if config.writable:
                candidates.append((role, "code" in config.roles))
        if not candidates:
            return None
        coders = [role for role, is_coder in candidates if is_coder]
        return coders[0] if coders else candidates[0][0]


    def message_add(self, task_id, message_id, recipient, content, allow_write=None,
                    context_files=None, referenced_task_ids=None, rounds=1, tool_files=None):
        require(string(message_id) and len(message_id) <= 128 and string(recipient)
                and string(content) and len(content) <= 20000,
                "Invalid message", "INVALID_INPUT")
        require(allow_write is None or type(allow_write) is bool, "allow_write must be boolean", "INVALID_INPUT")
        require(type(rounds) is int and rounds in (1, 2), "rounds must be 1 or 2", "INVALID_INPUT")
        if tool_files is not None:
            from .read_tools import validate_tool_files
            validate_tool_files(tool_files)
        if context_files is not None:
            require(strings(context_files) and len(context_files) <= 10, "Invalid context_files", "INVALID_INPUT")
        if referenced_task_ids is not None:
            require(strings(referenced_task_ids) and len(referenced_task_ids) <= 5, "Invalid referenced_task_ids", "INVALID_INPUT")
        from .agents import AgentRegistry
        registry = AgentRegistry(self)
        available = [a["id"] for a in registry.list(enabled_only=True) if a["ready"]]
        with self.transaction() as db:
            t = self._task(db, task_id)
            row = db.execute("SELECT body FROM messages WHERE task_id=? AND id=?", (task_id, message_id)).fetchone()
            if row:
                old = json.loads(row[0])
                require(old["recipient"] == recipient and old["content"] == content, "Message ID conflict", "CONFLICT")
                return old
            if recipient == "all":
                participants = [key for key in available if not t.get("team") or key in t["team"]["members"]]
            elif recipient == "both":
                participants = [a for a in ("codex", "antigravity") if a in available]
                require(len(participants) == 2, "Both built-in agents must be enabled", "UNSUPPORTED_PROVIDER")
            elif "," in recipient:
                requested = [part.strip() for part in recipient.split(",") if part.strip()]
                require(bool(requested) and len(requested) == len(set(requested)),
                        "Invalid recipient list", "INVALID_INPUT")
                require(all(r in available for r in requested),
                        "Agent is unknown, disabled or not configured", "UNSUPPORTED_PROVIDER")
                participants = requested
            else:
                require(recipient in available, "Agent is unknown, disabled or not configured", "UNSUPPORTED_PROVIDER")
                participants = [recipient]
            require(not t.get("team") or all(key in t["team"]["members"] for key in participants),
                    "Recipient is outside this task's team", "INVALID_INPUT")
            require(bool(participants) and len(participants) <= 12, "No ready agents or too many participants", "INVALID_INPUT")
            m = {"id": message_id, "task_id": task_id, "sender": "user", "recipient": recipient,
                 "content": content, "participants": participants, "revision": t.get("revision", 1),
                 "status": "QUEUED", "round": 1, "rounds": rounds, "replies": [], "created_at": time.time()}
            writable = t.get("config", {}).get("allow_write", False) if allow_write is None else allow_write
            require(not writable or t["config"].get("allow_write") is True,
                    "Enable session writing before requesting a writable message", "APPROVAL_REQUIRED")
            m["room_members"] = list(participants)
            m["write_epoch"] = t["config"].get("write_epoch", 0)
            if writable:
                m["allow_write"] = True
            if context_files:
                m["context_files"] = [f.strip() for f in context_files if f.strip()]
            if tool_files:
                m["tool_files"] = list(tool_files)
            if referenced_task_ids:
                m["referenced_task_ids"] = [tid.strip() for tid in referenced_task_ids if tid.strip()]
            db.execute("INSERT INTO messages VALUES(?,?,?)", (message_id, task_id, encode(m)))
            self._save(db, t, "MESSAGE_SAVED", {"message_id": message_id, "recipient": recipient})
            return m

    def chat_config(self, task_id, patch=None):
        """Session-level group chat settings: write grant and automatic round budget."""
        with self.transaction() as db:
            t = self._task(db, task_id)
            config = t.setdefault("config", {})
            config.setdefault("allow_write", False)
            config.setdefault("max_auto_rounds", 2)
            if patch:
                require(set(patch) <= {"allow_write", "max_auto_rounds"},
                        "Only allow_write and max_auto_rounds can be changed", "INVALID_INPUT")
                if "allow_write" in patch:
                    require(type(patch["allow_write"]) is bool, "allow_write must be boolean", "INVALID_INPUT")
                    if config["allow_write"] and not patch["allow_write"]:
                        # Re-enabling must not revive queued/held/in-flight old grants.
                        config["write_epoch"] = config.get("write_epoch", 0) + 1
                    config["allow_write"] = patch["allow_write"]
                if "max_auto_rounds" in patch:
                    require(type(patch["max_auto_rounds"]) is int and 0 <= patch["max_auto_rounds"] <= 8,
                            "max_auto_rounds must be 0-8", "INVALID_INPUT")
                    config["max_auto_rounds"] = patch["max_auto_rounds"]
                self._save(db, t, "CHAT_CONFIG_CHANGED", dict(config))
            return {"task_id": task_id, "allow_write": config["allow_write"],
                    "max_auto_rounds": config["max_auto_rounds"],
                    "auto_rounds_used": t.get("auto_rounds_used", 0)}

    def chat_release(self, task_id):
        """Release agent-authored messages that were held back by the round budget."""
        with self.transaction() as db:
            t = self._task(db, task_id)
            held = [json.loads(r[0]) for r in db.execute(
                "SELECT body FROM messages WHERE task_id=? ORDER BY rowid", (task_id,))]
            released = 0
            for m in held:
                if m.get("held") and m["status"] == "HELD":
                    m["status"] = "QUEUED"
                    m.pop("held", None)
                    m.pop("wait_reason", None)
                    db.execute("UPDATE messages SET body=? WHERE task_id=? AND id=?",
                               (encode(m), task_id, m["id"]))
                    released += 1
            if released:
                # Releasing is an explicit user decision, so it refreshes the budget.
                t["auto_rounds_used"] = 0
                self._save(db, t, "CHAT_RELEASED", {"message_ids": released})
            return {"released": released}

    def message_resolve(self, task_id, message_id, action="skip"):
        require(action in ("skip", "retry"), "action must be skip or retry", "INVALID_INPUT")
        with self.transaction() as db:
            t = self._task(db, task_id)
            row = db.execute("SELECT body FROM messages WHERE task_id=? AND id=?", (task_id, message_id)).fetchone()
            require(row is not None, "Unknown message", "NOT_FOUND")
            m = json.loads(row[0])
            require(m["status"] in ("BLOCKED", "RUNNING", "QUEUED"), "Message is not blocked or in flight", "INVALID_STATE")
            round_no = m.get("round", 1)
            chat_dispatches = [d for d in t["dispatches"]
                               if d["kind"] == "CHAT" and d["payload"].get("message_id") == message_id
                               and d["status"] in ("BLOCKED", "ACTIVE")]
            failed_roles = []
            for d in chat_dispatches:
                self._require_process_stopped(db, d["id"])
                d["status"] = "SUPERSEDED"
                failed_roles.append(d["role"])
            roles = self._chat_roles(m)
            if action == "skip":
                _, _, _, failed_now, _ = self._round_state(t, m)
                targets = list(dict.fromkeys(list(failed_roles) + sorted(failed_now)))
                for role in targets or [roles[0]]:
                    for existing in list(m["replies"]):
                        if existing["role"] == role and existing.get("round", 1) == round_no:
                            m["replies"].remove(existing)
                    m["replies"].append({
                        "role": role,
                        "content": "_（已跳过该 Agent 的本次回复）_",
                        "skipped": True,
                        "round": round_no,
                        "ok": True,
                    })
                m.pop("error", None)
                _, _, _, still_failed, pending = self._round_state(t, m)
                if still_failed:
                    m["status"] = "BLOCKED"
                elif pending:
                    m["status"] = "QUEUED"
                elif round_no < m.get("rounds", 1):
                    m["round"] = round_no + 1
                    m["status"] = "QUEUED"
                else:
                    m["status"] = "DELIVERED"
            else:
                # Retry removes the failed attempt so the round re-dispatches it.
                for role in failed_roles:
                    m["replies"] = [r for r in m["replies"]
                                    if not (r["role"] == role and r.get("round", 1) == round_no)]
                m.pop("error", None)
                m["status"] = "QUEUED"
            db.execute("UPDATE messages SET body=? WHERE task_id=? AND id=?", (encode(m), task_id, message_id))
            self._status(t)
            self._save(db, t, "MESSAGE_RESOLVED", {"message_id": message_id, "action": action})
            return m

    def chat_dispatch_round(self, task_id):
        """Issue every dispatch of the current round atomically.

        Callers run the returned dispatches concurrently: one agent per dispatch,
        each with its own CLI session. Round 1 is a true simultaneous broadcast
        (no peer replies); later rounds let everyone see the previous round.
        """
        with self.transaction() as db:
            t = self._task(db, task_id)
            if self._chat_wait_reason(db, t):
                return []
            messages = [json.loads(r[0]) for r in db.execute("SELECT body FROM messages WHERE task_id=? ORDER BY rowid", (task_id,))]
            m = next((x for x in messages if x["status"] == "QUEUED"), None)
            if m is None:
                return []
            if m["revision"] != t.get("revision", 1):
                m["status"] = "NEEDS_REVIEW"
                db.execute("UPDATE messages SET body=? WHERE task_id=? AND id=?", (encode(m), task_id, m["id"]))
                return []
            roles = self._chat_roles(m)
            round_no = m.get("round", 1)
            _, _, answered, failed_roles, pending = self._round_state(t, m)
            if not pending:
                if failed_roles:
                    m["status"] = "BLOCKED"
                    m["error"] = "；".join(f"{role}: 回复中断" for role in sorted(failed_roles))
                else:
                    m["status"] = "DELIVERED"
                db.execute("UPDATE messages SET body=? WHERE task_id=? AND id=?", (encode(m), task_id, m["id"]))
                return []
            writer = self._pick_writer(db, t, m, pending)
            room_members = self._room_members(db, t, m)
            brief = lambda replies: [{"role": r["role"], "content": r["content"][:2000]}
                                     for r in replies if not r.get("skipped")]
            # Round 1 is simultaneous: nobody sees a peer before answering.
            peer_replies = [] if round_no == 1 else brief(self._round_replies(m, round_no - 1))
            recent = [{"sender": x.get("sender", "user"), "content": x["content"],
                       "replies": brief(x["replies"])}
                      for x in messages if x["status"] == "DELIVERED"][-4:]
            issues = []
            for role in pending:
                if self._chat_member_wait_reason(db, t, m, role):
                    continue
                d = self._issue(t, "CHAT", role=role)
                d["payload"].update(message_id=m["id"], message=m["content"],
                                    message_sender=m.get("sender", "user"),
                                    message_intent=m.get("intent"),
                                    round=round_no, total_rounds=m.get("rounds", 1),
                                    peer_replies=peer_replies, recent_messages=recent,
                                    room_members=room_members,
                                    write_epoch=m.get("write_epoch", 0),
                                    is_auto=bool(m.get("auto")))
                # Explicit per-user-message grant; never inherited by peer messages.
                if m.get("sender") == "user" and not m.get("auto") and m.get("tool_files"):
                    d["payload"]["tool_files"] = list(m["tool_files"])
                if writer == role:
                    d["payload"]["allow_write"] = True
                p = self._project(db, t["project_id"])
                if p.get("memory"):
                    d["payload"]["project_memory"] = p["memory"]
                # Every chat turn carries a bounded orientation block so a
                # chat-only or CLI agent can answer about this project without
                # the user pasting README or file paths by hand.
                from .workspace import project_digest
                digest = project_digest(p["path"])
                if digest:
                    d["payload"]["project_context"] = digest
                if m.get("context_files"):
                    from .workspace import read_project_files
                    # A tool-authorized file must not bypass its secure reader
                    # via the legacy attachment path (which can follow links).
                    attachments = [f for f in m["context_files"] if f not in m.get("tool_files", [])]
                    d["payload"]["attached_files"] = read_project_files(p["path"], attachments)
                if m.get("referenced_task_ids"):
                    ref_sessions = []
                    for ref_id in m["referenced_task_ids"]:
                        ref_row = db.execute("SELECT body FROM tasks WHERE id=? AND project_id=?",
                                             (ref_id, t["project_id"])).fetchone()
                        if ref_row:
                            ref_task = json.loads(ref_row[0])
                            ref_msgs = [json.loads(r[0]) for r in db.execute(
                                "SELECT body FROM messages WHERE task_id=? ORDER BY rowid DESC LIMIT 2", (ref_id,))]
                            excerpts = []
                            for rm in reversed(ref_msgs):
                                for rep in rm.get("replies", []):
                                    if not rep.get("skipped"):
                                        excerpts.append(f"{rep['role']}: {rep['content'][:600]}")
                            summary_text = " | ".join(excerpts) if excerpts else ref_task.get("requirement", "")[:600]
                            ref_sessions.append({"id": ref_id, "title": ref_task["title"], "summary": summary_text})
                    if ref_sessions:
                        d["payload"]["referenced_sessions"] = ref_sessions
                issues.append(d)
            if not issues:
                return []
            m["status"] = "RUNNING"
            if len(issues) > 1:
                m["parallel"] = True
            db.execute("UPDATE messages SET body=? WHERE task_id=? AND id=?", (encode(m), task_id, m["id"]))
            self._save(db, t, "MESSAGE_DISPATCHED",
                       {"message_id": m["id"], "round": round_no,
                        "dispatches": [{"id": d["id"], "role": d["role"]} for d in issues]})
            return issues

    def chat_dispatch(self, task_id):
        """Single-dispatch compatibility wrapper used by the CLI and older tests."""
        issues = self.chat_dispatch_round(task_id)
        return issues[0] if issues else None

    def tasks_list(self, project_id=None):
        with self.transaction() as db:
            if project_id:
                self._project(db, project_id)
                rows = db.execute("SELECT body FROM tasks WHERE project_id=? ORDER BY rowid DESC", (project_id,)).fetchall()
            else:
                rows = db.execute("SELECT body FROM tasks ORDER BY rowid DESC").fetchall()
            tasks = []
            for r in rows:
                t = json.loads(r["body"])
                tasks.append({
                    "id": t["id"],
                    "project_id": t["project_id"],
                    "title": t["title"],
                    "requirement": t["requirement"],
                    "status": t["status"],
                    "phase": t["phase"],
                    "block_reason": t.get("block_reason"),
                    "created_at": t.get("created_at"),
                    "updated_at": t.get("updated_at"),
                    "steps_count": len(t.get("steps", [])),
                    "dispatches_count": len(t.get("dispatches", []))
                })
            return tasks

    def show(self, task):
        with self.transaction() as db:
            result = self._task(db, task)
            result["events"] = [{"seq": r["seq"], "created_at": r["created_at"],
                                 "kind": r["kind"], "data": json.loads(r["body"])}
                                for r in db.execute("SELECT * FROM events WHERE task_id=? ORDER BY seq", (task,))]
            return result

    def _get_dispatch(self, task, dispatch):
        result = next((d for d in task["dispatches"] if d["id"] == dispatch), None)
        require(result is not None, "Dispatch does not belong to this task", "NOT_FOUND")
        return result

    def _busy(self, db, task, role):
        # Provider sessions belong to tasks, not projects. Keep unresolved work
        # locked in its own task; CODE/FIX/VERIFY checkout safety is enforced
        # separately by _workspace_busy for every provider.
        return any(d["status"] in ("ACTIVE", "BLOCKED") and d["role"] == role
                   for d in task["dispatches"])

    def _workspace_busy(self, db, task, kind, exclude_dispatch_id=None):
        # Without a pinned snapshot, CODE/FIX must not change the checkout
        # while any task in the same project is verifying it (or vice versa).
        if kind not in ("CODE", "FIX", "VERIFY"):
            return False
        for row in db.execute("SELECT body FROM tasks WHERE project_id=?", (task["project_id"],)):
            other = json.loads(row[0])
            if any(d["id"] != exclude_dispatch_id and d["status"] in ("ACTIVE", "BLOCKED")
                   and (d["kind"] in ("CODE", "FIX", "VERIFY")
                        or (d["kind"] == "CHAT" and d["payload"].get("allow_write")))
                   for d in other["dispatches"]):
                return True
        return False

    def _issue(self, task, kind, step=None, attempt=1, role=None):
        now = time.time()
        if role is None:
            if kind in ("CODE", "FIX", "ANALYZE") and step and step.get("assignee"):
                role = step["assignee"]
            elif kind == "VERIFY" and step and step.get("verifier"):
                role = step["verifier"]
            elif task.get("team") and kind in ("PLAN", "PLAN_FINALIZE", "PLAN_REVIEW"):
                role = task["team"]["reviewer" if kind == "PLAN_REVIEW" else "coordinator"]
            else:
                role = "antigravity" if kind in ("PLAN_REVIEW", "VERIFY") else "codex"
        payload = {"requirement": task["requirement"], "draft_plan": task["draft_plan"],
                   "requirement_revision": task.get("revision", 1),
                   "plan_review": task["plan_review"], "step": step}
        if task.get("team") and kind in ("PLAN", "CLAIM", "PLAN_REVIEW", "PLAN_FINALIZE"):
            payload["team_inputs"] = [
                {"agent": d["role"], "kind": d["kind"], "summary": d["report"]["summary"][:2000],
                 "suggestions": d["report"]["suggestions"], "data": d["report"]["data"]}
                for d in task["dispatches"] if d["kind"] in ("CONSULT", "CLAIM")
                and d["status"] == "DONE" and d.get("report", {}).get("status") == "DONE"
                and d["payload"]["requirement_revision"] == task.get("revision", 1)]
        if step:
            by_id = {item["id"]: item for item in task["steps"]}
            payload["dependency_outputs"] = [
                {"step_id": dep, "work_kind": by_id[dep].get("work_kind", "code"),
                 "summary": by_id[dep]["handoff"]["summary"][:2000],
                 "deliverable": by_id[dep]["handoff"]["data"].get("deliverable", "")[:4000]}
                for dep in step["depends_on"] if dep in by_id and by_id[dep].get("handoff")]
        # Copy payload: later state updates must not mutate a dispatched contract.
        d = {"id": uid(), "task_id": task["id"], "step_id": step["id"] if step else None,
             "kind": kind, "role": role, "attempt": attempt, "status": "ACTIVE",
             "version": step["version"] if step else None, "created_at": now,
             "deadline": now + task["config"]["timeout_seconds"],
             "payload": json.loads(encode(payload)), "report": None}
        task["dispatches"].append(d)
        return d

    def dispatch(self, task):
        with self.transaction() as db:
            t = self._task(db, task)
            if t["status"] != "ACTIVE":
                return {"dispatch": None}
            if any(json.loads(r[0])["status"] in ("QUEUED", "RUNNING") for r in db.execute("SELECT body FROM messages WHERE task_id=?", (task,))):
                return {"dispatch": None}
            kind = {"PLANNING": "PLAN", "PLAN_REVIEW": "PLAN_REVIEW", "PLAN_FINALIZE": "PLAN_FINALIZE"}.get(t["phase"])
            if t.get("team") and kind in ("PLAN", "PLAN_REVIEW"):
                required_round = "CONSULT" if kind == "PLAN" else "CLAIM"
                completed_members = {d["role"] for d in t["dispatches"]
                                     if d["kind"] == required_round and d["status"] == "DONE"
                                     and d["report"]["status"] == "DONE"
                                     and d["payload"]["requirement_revision"] == t.get("revision", 1)}
                if completed_members != set(t["team"]["members"]):
                    return {"dispatch": None, "reason": "TEAM_ROUND_REQUIRED"}
            candidates = []
            if kind:
                candidates.append((kind, None))
            else:
                complete = {s["id"] for s in t["steps"] if s["status"] == "COMPLETED"}
                for s in t["steps"]:
                    if s["status"] == "VERIFYING":
                        candidates.append(("VERIFY", s))
                    elif s["status"] == "FIXING":
                        candidates.append(("FIX", s))
                    elif s["status"] == "PENDING" and set(s["depends_on"]) <= complete:
                        candidates.append(("ANALYZE" if s.get("work_kind", "code") == "analysis" else "CODE", s))
            workspace_busy, unavailable = False, False
            for kind, step in candidates:
                role = (step.get("assignee") if step and kind in ("CODE", "FIX", "ANALYZE") else
                        step.get("verifier") if step and kind == "VERIFY" else
                        t["team"]["reviewer" if kind == "PLAN_REVIEW" else "coordinator"]
                        if t.get("team") and kind in ("PLAN", "PLAN_REVIEW", "PLAN_FINALIZE") else None
                        ) or ("antigravity" if kind in ("PLAN_REVIEW", "VERIFY") else "codex")
                agent_row = db.execute("SELECT body FROM agents WHERE id=?", (role,)).fetchone()
                if agent_row:
                    from .agents import AgentConfig
                    if not AgentConfig(json.loads(agent_row[0])).ready:
                        unavailable = True
                        continue
                if self._workspace_busy(db, t, kind):
                    workspace_busy = True
                    continue
                if self._busy(db, t, role):
                    continue
                if step and kind == "CODE":
                    step["status"] = "CODING"
                elif step and kind == "ANALYZE":
                    step["status"] = "ANALYZING"
                d = self._issue(t, kind, step, role=role)
                if kind in ("PLAN", "PLAN_FINALIZE"):
                    from .agents import AgentConfig
                    messages = [json.loads(r[0]) for r in db.execute(
                        "SELECT body FROM messages WHERE task_id=? ORDER BY rowid DESC LIMIT 5", (task,))]
                    d["payload"]["team_discussion"] = [
                        {"question": m["content"][:2000],
                         "replies": [{"agent": r["role"], "opinion": r["content"][:1200]}
                                     | ({"proposals": r["report"]["data"]["proposals"]}
                                        if r["report"]["data"].get("proposals") else {})
                                     for r in m["replies"]]}
                        for m in reversed(messages) if m["status"] == "DELIVERED"
                        and m["revision"] == t.get("revision", 1)]
                    d["payload"]["available_agents"] = [
                        {"id": a.id, "roles": a.roles}
                        for a in (AgentConfig(json.loads(r[0])) for r in db.execute("SELECT body FROM agents ORDER BY rowid"))
                        if a.ready and (not t.get("team") or a.id in t["team"]["members"])]
                    if t.get("team"):
                        available = d["payload"]["available_agents"]
                        d["payload"]["executable_work_kinds"] = [work for work in ("code", "analysis")
                            if any(work in a["roles"] and any(b["id"] != a["id"] and "verify" in b["roles"]
                                   for b in available) for a in available)]
                self._save(db, t, "DISPATCHED", {"dispatch_id": d["id"], "kind": kind})
                return {"dispatch": d}
            return {"dispatch": None, "reason": "PROJECT_WORKSPACE_BUSY" if workspace_busy else
                    "AGENT_UNAVAILABLE" if unavailable else "NO_ELIGIBLE_STEP"}

    def agents_list(self, enabled_only=False):
        from .agents import AgentRegistry
        return AgentRegistry(self).list(enabled_only=enabled_only)

    def agent_add(self, agent_data):
        from .agents import AgentRegistry
        return AgentRegistry(self).register(agent_data)

    def _validate(self, t, d, report):
        exact(report, ["schema_version", "task_id", "dispatch_id", "role", "attempt", "status", "summary", "basis", "suggestions", "next_action", "data"])
        require(type(report["schema_version"]) is int and report["schema_version"] == 1, "Unsupported schema")
        require(report["task_id"] == t["id"] and report["dispatch_id"] == d["id"] and report["role"] == d["role"], "Report identity mismatch")
        require(type(report["attempt"]) is int and report["attempt"] == d["attempt"], "Attempt mismatch", "STALE_REPORT")
        require(string(report["summary"]) and strings(report["basis"], True) and strings(report["suggestions"])
                and string(report["next_action"]), "Report needs outcome, basis and next action")
        data, status = report["data"], report["status"]
        if status == "BLOCKED":
            exact(data, ["reason"])
            require(string(data["reason"]), "Blocked reason required")
            return
        kind = d["kind"]
        if kind == "VERIFY":
            require(status in ("PASS", "FAIL"), "Verification must PASS, FAIL or BLOCKED")
            exact(data, ["version", "checks", "findings"])
            require(data["version"] == d["version"], "Tested version mismatch", "STALE_REPORT")
            require(isinstance(data["checks"], list) and isinstance(data["findings"], list), "Checks and findings must be lists")
            criteria = d["payload"]["step"]["acceptance"]
            checked = []
            for check in data["checks"]:
                exact(check, ["criterion", "status", "evidence"])
                require(string(check["criterion"]) and check["status"] in ("PASS", "FAIL") and strings(check["evidence"], True), "Invalid check or missing evidence")
                checked.append(check["criterion"])
            require(sorted(checked) == sorted(criteria), "Every criterion must be checked exactly once")
            for finding in data["findings"]:
                exact(finding, ["problem", "reproduction", "expected", "actual", "evidence"])
                require(all(string(finding[k]) for k in ("problem", "expected", "actual"))
                        and strings(finding["reproduction"], True) and strings(finding["evidence"], True), "Finding needs reproduction and evidence")
            failed = any(c["status"] == "FAIL" for c in data["checks"])
            require((status == "PASS" and not failed and not data["findings"])
                    or (status == "FAIL" and failed and bool(data["findings"])), "Verdict contradicts evidence")
        else:
            require(status == "DONE", "Dispatch must be DONE or BLOCKED")
            if kind in ("PLAN", "PLAN_FINALIZE"):
                plan_validate(data)
            elif kind == "PLAN_REVIEW":
                exact(data, [])
                require(len(report["suggestions"]) <= 3, "Review allows at most 3 suggestions")
            elif kind == "CONSULT":
                exact(data, [])
                require(len(report["summary"]) <= 4000 and len(report["suggestions"]) <= 3,
                        "Consultation must be concise")
            elif kind == "CLAIM":
                exact(data, ["claims"])
                claims = data["claims"]
                require(isinstance(claims, list) and len(claims) <= 100, "Invalid claims")
                steps = {s["id"]: s for s in d["payload"]["draft_plan"]["steps"]}
                seen = set()
                for claim in claims:
                    exact(claim, ["step_id", "reason"])
                    require(string(claim["step_id"]) and claim["step_id"] in steps
                            and claim["step_id"] not in seen and string(claim["reason"])
                            and len(claim["reason"]) <= 1000, "Invalid or duplicate step claim")
                    require(steps[claim["step_id"]].get("work_kind", "code") in d["payload"]["capabilities"],
                            "Claim exceeds adapter capabilities")
                    seen.add(claim["step_id"])
            elif kind == "CHAT":
                require(data == {} or (isinstance(data, dict) and set(data) <= {"proposals", "messages"}),
                        "Chat data must be empty, proposals or messages")
                if data.get("proposals"):
                    proposals = data["proposals"]
                    require(isinstance(proposals, list) and len(proposals) <= 3,
                            "At most three work proposals per reply")
                    for proposal in proposals:
                        exact(proposal, ["title", "work_kind", "assignee", "reason"])
                        require(string(proposal["title"]) and len(proposal["title"]) <= 200
                                and proposal["work_kind"] in ("code", "analysis")
                                and string(proposal["assignee"]) and len(proposal["assignee"]) <= 64
                                and string(proposal["reason"]) and len(proposal["reason"]) <= 1000,
                                "Invalid work proposal")
                if data.get("messages"):
                    outgoing = data["messages"]
                    require(isinstance(outgoing, list) and len(outgoing) <= 3,
                            "At most three peer messages per reply")
                    room = set(d["payload"].get("room_members") or [])
                    for item in outgoing:
                        exact(item, ["to", "intent", "content"])
                        require(string(item["to"]) and len(item["to"]) <= 64
                                and item["intent"] in ("ask", "assign", "claim", "report")
                                and string(item["content"]) and len(item["content"]) <= 4000,
                                "Invalid peer message")
                        require(item["to"] != d["role"], "An agent cannot address itself")
                        require((item["to"] == "all" and item["intent"] in ("claim", "report"))
                                or (item["to"] != "all" and item["to"] in room),
                                "Peer message target is not in this room")
                require(len(report["suggestions"]) <= 3, "Chat allows at most 3 suggestions")
            elif kind == "ANALYZE":
                exact(data, ["deliverable", "evidence"])
                require(string(data["deliverable"]) and len(data["deliverable"]) <= 50000
                        and strings(data["evidence"], True) and len(data["evidence"]) <= 30
                        and all(len(item) <= 2000 for item in data["evidence"]),
                        "Analysis needs a deliverable and evidence")
            else:
                exact(data, ["version", "changes", "known_limits"])
                require(string(data["version"]) and strings(data["changes"], True) and strings(data["known_limits"]), "Invalid handoff")
                if kind == "FIX":
                    require(data["version"] != d["version"], "Fix must produce a new version", "STALE_REPORT")

    def _status(self, t):
        if t["phase"] == "EXECUTING" and t["steps"] and all(s["status"] == "COMPLETED" for s in t["steps"]):
            t["phase"] = "COMPLETED"
        blocked = any(d["status"] == "BLOCKED" for d in t["dispatches"]) or any(s["status"] == "BLOCKED" for s in t["steps"])
        if any(s.get("repair_exhausted", False) for s in t["steps"]):
            t["block_reason"] = "REPAIR_LIMIT"
        if t["status"] != "PAUSED":
            t["status"] = "BLOCKED" if blocked else ("COMPLETED" if t["phase"] == "COMPLETED" else "ACTIVE")
        if not blocked:
            t["block_reason"] = None

    def submit(self, task, report):
        require(isinstance(report, dict), "Report must be an object")
        with self.transaction() as db:
            t = self._task(db, task)
            d = self._get_dispatch(t, report.get("dispatch_id"))
            if d["report"] is not None:
                require(d["report"] == report, "Different report already accepted", "CONFLICT")
                return {"accepted": True, "duplicate": True}
            require(d["status"] == "ACTIVE", "Dispatch no longer accepts results", "STALE_REPORT")
            self._require_process_stopped(db, d["id"])
            require(time.time() <= d["deadline"], "Dispatch deadline exceeded; expire then retry", "STALE_REPORT")
            self._validate(t, d, report)
            d["report"] = report
            d["status"] = "DONE"
            step = next((s for s in t["steps"] if s["id"] == d["step_id"]), None)
            data = report["data"]
            if d["kind"] == "CHAT":
                row = db.execute("SELECT body FROM messages WHERE task_id=? AND id=?", (task, d["payload"]["message_id"])).fetchone()
                m = json.loads(row[0])
                round_no = d["payload"].get("round", m.get("round", 1))
                m["replies"].append({"role": d["role"], "content": report["summary"], "report": report,
                                     "round": round_no, "ok": report["status"] != "BLOCKED",
                                     "dispatch_id": d["id"]})
                roles, _, _, failed, pending = self._round_state(t, m)
                if pending:
                    # Other agents of this parallel round are still working.
                    m["status"] = "RUNNING"
                elif failed:
                    m["status"] = "BLOCKED"
                    reasons = {r["role"]: (r.get("report", {}).get("data", {}) or {}).get("reason")
                               for r in m["replies"] if not r.get("ok", True)}
                    m["error"] = "；".join(
                        f"{role}: {reasons.get(role) or '回复中断'}" for role in sorted(failed))
                elif round_no < m.get("rounds", 1):
                    m["round"] = round_no + 1
                    m["status"] = "QUEUED"
                else:
                    m["status"] = "DELIVERED"
                db.execute("UPDATE messages SET body=? WHERE task_id=? AND id=?", (encode(m), task, m["id"]))
                if report["status"] != "BLOCKED":
                    self._record_agent_messages(db, t, m, d, report)
            if report["status"] == "BLOCKED":
                d["status"] = "BLOCKED"
                t["block_reason"] = data["reason"]
                if step:
                    step["status"] = "BLOCKED"
            elif d["kind"] == "PLAN":
                t["draft_plan"] = data
                t["phase"] = "PLAN_REVIEW"
            elif d["kind"] == "PLAN_REVIEW":
                t["plan_review"] = report
                t["phase"] = "PLAN_FINALIZE"
            elif d["kind"] == "PLAN_FINALIZE":
                from .agents import AgentConfig
                data = json.loads(encode(data))
                agents = {a.id: a for a in (AgentConfig(json.loads(r[0]))
                         for r in db.execute("SELECT body FROM agents"))}
                if t.get("team"):
                    from .allocation import allocate
                    claims = [dict(c, agent=d["role"]) for d in t["dispatches"]
                              if d["kind"] == "CLAIM" and d["status"] == "DONE"
                              and d["report"]["status"] == "DONE"
                              and d["payload"]["requirement_revision"] == t.get("revision", 1)
                              for c in d["report"]["data"]["claims"]]
                    data = {"steps": allocate(data["steps"], agents, t["team"]["members"], claims)}
                for planned in data["steps"]:
                    kind = planned.get("work_kind", "code")
                    assignee = planned.get("assignee", "codex")
                    if assignee == "antigravity" and "verifier" not in planned:
                        planned["verifier"] = "codex"
                    require(kind != "code" or (assignee in agents and agents[assignee].ready
                            and "code" in agents[assignee].roles) or assignee == "codex" and not t.get("team"),
                            "Code assignee is not ready or capable", "INVALID_PLAN")
                    verifier = planned.get("verifier", "antigravity")
                    require(verifier != assignee, "Author cannot verify their own work", "INVALID_PLAN")
                    if "verifier" in planned:
                        require(verifier in agents and agents[verifier].ready and "verify" in agents[verifier].roles,
                                "Verifier is not ready or capable", "INVALID_PLAN")
                    if kind == "analysis" and assignee != "codex":
                        agent = agents.get(assignee)
                        require(agent is not None and agent.ready and "analysis" in agent.roles,
                                "Analysis assignee is not ready or capable", "INVALID_PLAN")
                t["draft_plan"] = data
                t["steps"] = [dict(s, status="PENDING", repair_count=0, version=None, handoff=None, verification=None) for s in data["steps"]]
                t["phase"] = "EXECUTING"
            elif d["kind"] in ("CODE", "FIX"):
                step.update(status="VERIFYING", version=data["version"], handoff=report)
            elif d["kind"] == "ANALYZE":
                version = hashlib.sha256(encode(data).encode("utf-8")).hexdigest()
                step.update(status="VERIFYING", version=version, handoff=report)
            elif d["kind"] == "VERIFY":
                step["verification"] = report
                if report["status"] == "PASS":
                    step["status"] = "COMPLETED"
                elif step["repair_count"] < t["config"]["max_repairs"]:
                    step["repair_count"] += 1
                    step["status"] = "PENDING" if step.get("work_kind", "code") == "analysis" else "FIXING"
                else:
                    step["status"] = "BLOCKED"
                    step["repair_exhausted"] = True
                    t["block_reason"] = "REPAIR_LIMIT"
            self._status(t)
            self._save(db, t, "RESULT_ACCEPTED", {"dispatch_id": d["id"], "status": report["status"]})
            return {"accepted": True, "duplicate": False}

    def pause(self, task):
        with self.transaction() as db:
            t = self._task(db, task)
            require(t["status"] != "COMPLETED", "Completed task cannot pause", "INVALID_STATE")
            if t["status"] != "PAUSED":
                t["status"] = "PAUSED"
                self._save(db, t, "PAUSED", {})
            return {"status": t["status"]}

    def resume(self, task):
        with self.transaction() as db:
            t = self._task(db, task)
            require(t["status"] == "PAUSED", "Only paused tasks can resume", "INVALID_STATE")
            t["status"] = "ACTIVE"
            self._status(t)
            self._save(db, t, "RESUMED", {})
            return {"status": t["status"]}

    def retry(self, task, dispatch_id):
        with self.transaction() as db:
            t = self._task(db, task)
            require(t["status"] == "BLOCKED" and t["block_reason"] != "REPAIR_LIMIT", "Task cannot retry", "INVALID_STATE")
            d = self._get_dispatch(t, dispatch_id)
            require(d["status"] == "BLOCKED", "Only blocked dispatches can retry", "INVALID_STATE")
            self._require_process_stopped(db, d["id"])
            d["status"] = "SUPERSEDED"
            require(not self._busy(db, t, d["role"]), "Another dispatch occupies this role", "INVALID_STATE")
            require(not self._workspace_busy(db, t, d["kind"], d["id"]),
                    "Another code or verification step occupies this project", "INVALID_STATE")
            step = next((s for s in t["steps"] if s["id"] == d["step_id"]), None)
            if step:
                step["status"] = {"CODE": "CODING", "ANALYZE": "ANALYZING", "FIX": "FIXING", "VERIFY": "VERIFYING"}[d["kind"]]
            new = self._issue(t, d["kind"], step, d["attempt"] + 1, role=d["role"])
            if d["kind"] in ("CONSULT", "CLAIM"):
                new["payload"] = json.loads(encode(d["payload"]))
            if d["kind"] == "CHAT":
                new["role"], new["payload"] = d["role"], json.loads(encode(d["payload"]))
                row = db.execute("SELECT body FROM messages WHERE task_id=? AND id=?", (task, d["payload"]["message_id"])).fetchone()
                m = json.loads(row[0])
                m["replies"] = [r for r in m["replies"] if r["role"] != d["role"]]
                m["status"] = "RUNNING"
                db.execute("UPDATE messages SET body=? WHERE task_id=? AND id=?", (encode(m), task, m["id"]))
            self._status(t)
            self._save(db, t, "RETRIED", {"old_dispatch_id": d["id"], "dispatch_id": new["id"]})
            return {"dispatch": new}

    def _require_process_stopped(self, db, dispatch_id):
        if db.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='runs'").fetchone():
            row = db.execute("SELECT body FROM runs WHERE dispatch_id=?", (dispatch_id,)).fetchone()
            if row:
                require(json.loads(row["body"])["status"] not in ("PREPARED", "RUNNING"),
                        "Provider may still be running; inspect and resolve first", "INVALID_STATE")

    def expire(self, task):
        with self.transaction() as db:
            t = self._task(db, task)
            expired = []
            for d in t["dispatches"]:
                if d["status"] == "ACTIVE" and d["deadline"] < time.time():
                    d["status"] = "BLOCKED"
                    if d["kind"] == "CHAT":
                        row = db.execute("SELECT body FROM messages WHERE task_id=? AND id=?", (task, d["payload"]["message_id"])).fetchone()
                        m = json.loads(row[0]); m.update(status="BLOCKED", error="Dispatch expired; inspect any running process before retry")
                        db.execute("UPDATE messages SET body=? WHERE task_id=? AND id=?", (encode(m), task, m["id"]))
                    expired.append(d["id"])
                    for s in t["steps"]:
                        if s["id"] == d["step_id"]:
                            s["status"] = "BLOCKED"
            if expired:
                t["block_reason"] = "TIMEOUT"
                self._status(t)
                self._save(db, t, "EXPIRED", {"dispatch_ids": expired})
            return {"expired": expired}

    def template(self, task, dispatch_id):
        with self.transaction() as db:
            t = self._task(db, task)
            d = self._get_dispatch(t, dispatch_id)
            data, status = {}, "DONE"
            if d["kind"] in ("PLAN", "PLAN_FINALIZE"):
                data = t["draft_plan"] or {"steps": [{"id": "s1", "title": "REPLACE", "acceptance": ["REPLACE"], "depends_on": []}]}
            elif d["kind"] == "CLAIM":
                data = {"claims": []}
            elif d["kind"] in ("CODE", "FIX"):
                data = {"version": "REPLACE_WITH_IMMUTABLE_VERSION", "changes": ["REPLACE"], "known_limits": []}
            elif d["kind"] == "ANALYZE":
                data = {"deliverable": "REPLACE", "evidence": ["REPLACE_WITH_CHECKABLE_REFERENCE"]}
            elif d["kind"] == "VERIFY":
                # A template must never look like a completed successful verification.
                status, data = "BLOCKED", {"reason": "Not yet executed; replace with checks and evidence"}
            return {"schema_version": 1, "task_id": task, "dispatch_id": dispatch_id,
                    "role": d["role"], "attempt": d["attempt"], "status": status,
                    "summary": "REPLACE", "basis": ["REPLACE"], "suggestions": [],
                    "next_action": "REPLACE", "data": data}

    def doctor(self, project):
        with self.transaction() as db:
            p = self._project(db, project)
        def git(*args):
            if not shutil.which("git"):
                return {"exit_code": None, "output": "git not found"}
            r = subprocess.run(["git", "-C", p["path"], *args], capture_output=True, text=True, timeout=10)
            return {"exit_code": r.returncode, "output": (r.stdout + r.stderr).strip()}
        return {"project": p, "git": git("status", "--short", "--branch"),
                "head": git("rev-parse", "HEAD"),
                "executables": {name: shutil.which(name) for name in ("codex", "agy", "antigravity")},
                "note": "PATH availability is not proof of non-interactive agent capability."}
