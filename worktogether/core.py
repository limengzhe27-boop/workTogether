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


def plan_validate(data):
    exact(data, ["steps"])
    steps = data["steps"]
    require(isinstance(steps, list) and 0 < len(steps) <= 100, "Plan needs 1–100 steps", "INVALID_PLAN")
    by_id = {}
    for step in steps:
        require(isinstance(step, dict) and set(step).issubset({"id", "title", "acceptance", "depends_on", "assignee", "work_kind"})
                and {"id", "title", "acceptance", "depends_on"}.issubset(set(step)),
                "Invalid step fields", "INVALID_PLAN")
        require(string(step["id"]) and string(step["title"]) and strings(step["acceptance"], True)
                and strings(step["depends_on"]), "Invalid step fields", "INVALID_PLAN")
        if "assignee" in step:
            require(string(step["assignee"]), "Invalid assignee", "INVALID_PLAN")
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
        return dict(row)

    def _task(self, db, task):
        row = db.execute("SELECT body FROM tasks WHERE id=?", (task,)).fetchone()
        require(row is not None, "Unknown task", "NOT_FOUND")
        return json.loads(row["body"])

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

    def task_add(self, project, title, requirement):
        require(string(title) and string(requirement), "Title and requirement required", "INVALID_INPUT")
        with self.transaction() as db:
            self._project(db, project)
            task = {"id": uid(), "project_id": project, "title": title, "requirement": requirement,
                    "revision": 1, "revisions": [],
                    "status": "ACTIVE", "phase": "PLANNING", "block_reason": None,
                    "config": {"max_repairs": 2, "timeout_seconds": 1800},
                    "draft_plan": None, "plan_review": None, "steps": [], "dispatches": [],
                    "created_at": time.time()}
            db.execute("INSERT INTO tasks VALUES(?,?,?)", (task["id"], project, encode(task)))
            self._save(db, task, "TASK_CREATED", {"title": title})
            return task

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
                                  ("planning_jobs", ("QUEUED", "RUNNING"))):
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
            task.update(requirement=requirement, revision=revision + 1, draft_plan=None,
                        plan_review=None, steps=[], phase="PLANNING", block_reason=None,
                        status="PAUSED" if task["status"] == "PAUSED" else "ACTIVE")
            if db.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='approvals'").fetchone():
                db.execute("DELETE FROM approvals WHERE task_id=?", (task_id,))
            self._save(db, task, "REQUIREMENT_REVISED", {"message_id": message_id,
                       "revision": revision + 1, "requirement": requirement})
            return {"revision": revision + 1, "duplicate": False}

    def projects_list(self):
        with self.transaction() as db:
            projects = [dict(r) for r in db.execute("SELECT * FROM projects ORDER BY name")]
            for p in projects:
                count = db.execute("SELECT COUNT(*) as c FROM tasks WHERE project_id=?", (p["id"],)).fetchone()["c"]
                p["task_count"] = count
            return projects

    def messages(self, task_id):
        with self.transaction() as db:
            self._task(db, task_id)
            return [json.loads(r[0]) for r in db.execute("SELECT body FROM messages WHERE task_id=? ORDER BY rowid", (task_id,))]

    def message_add(self, task_id, message_id, recipient, content):
        require(string(message_id) and len(message_id) <= 128 and string(recipient)
                and string(content) and len(content) <= 20000,
                "Invalid message", "INVALID_INPUT")
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
                participants = available
            elif recipient == "both":
                participants = [a for a in ("codex", "antigravity") if a in available]
                require(len(participants) == 2, "Both built-in agents must be enabled", "UNSUPPORTED_PROVIDER")
            else:
                require(recipient in available, "Agent is unknown, disabled or not configured", "UNSUPPORTED_PROVIDER")
                participants = [recipient]
            require(bool(participants) and len(participants) <= 12, "No ready agents or too many participants", "INVALID_INPUT")
            m = {"id": message_id, "task_id": task_id, "recipient": recipient, "content": content,
                 "participants": participants, "revision": t.get("revision", 1),
                 "status": "QUEUED", "replies": [], "created_at": time.time()}
            db.execute("INSERT INTO messages VALUES(?,?,?)", (message_id, task_id, encode(m)))
            self._save(db, t, "MESSAGE_SAVED", {"message_id": message_id, "recipient": recipient})
            return m

    def chat_dispatch(self, task_id):
        with self.transaction() as db:
            t = self._task(db, task_id)
            if t["status"] not in ("ACTIVE", "COMPLETED") or any(d["status"] == "ACTIVE" for d in t["dispatches"]):
                return None
            if db.execute("SELECT name FROM sqlite_master WHERE name='planning_jobs'").fetchone():
                if any(json.loads(r[0])["status"] in ("QUEUED", "RUNNING") for r in db.execute("SELECT body FROM planning_jobs WHERE task_id=?", (task_id,))):
                    return None
            if db.execute("SELECT name FROM sqlite_master WHERE name='runs'").fetchone():
                if any(json.loads(r[0])["status"] in ("PREPARED", "RUNNING", "EXITED") for r in db.execute("SELECT body FROM runs WHERE task_id=?", (task_id,))):
                    return None
            messages = [json.loads(r[0]) for r in db.execute("SELECT body FROM messages WHERE task_id=? ORDER BY rowid", (task_id,))]
            m = next((m for m in messages if m["status"] == "QUEUED"), None)
            if m is None:
                return None
            if m["revision"] != t.get("revision", 1):
                m["status"] = "NEEDS_REVIEW"
                db.execute("UPDATE messages SET body=? WHERE task_id=? AND id=?", (encode(m), task_id, m["id"]))
                return None
            roles = m.get("participants") or (["codex", "antigravity"] if m["recipient"] in ("both", "all") else [m["recipient"]])
            if len(m["replies"]) >= len(roles):
                m["status"] = "DELIVERED"
                db.execute("UPDATE messages SET body=? WHERE task_id=? AND id=?", (encode(m), task_id, m["id"]))
                return None
            role = roles[len(m["replies"])]
            if self._busy(db, t, role):
                return None
            d = self._issue(t, "CHAT", role=role)
            brief = lambda replies: [{"role": r["role"], "content": r["content"][:2000]} for r in replies]
            d["payload"].update(message_id=m["id"], message=m["content"], peer_replies=brief(m["replies"]),
                recent_messages=[{"content": x["content"], "replies": brief(x["replies"])}
                                 for x in messages if x["status"] == "DELIVERED"][-3:])
            m["status"] = "RUNNING"
            db.execute("UPDATE messages SET body=? WHERE task_id=? AND id=?", (encode(m), task_id, m["id"]))
            self._save(db, t, "MESSAGE_DISPATCHED", {"message_id": m["id"], "dispatch_id": d["id"], "role": role})
            return d

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
        if any(d["status"] in ("ACTIVE", "BLOCKED") and d["role"] == role for d in task["dispatches"]):
            return True
        if role == "codex":
            for row in db.execute("SELECT body FROM tasks WHERE project_id=? AND id!=?", (task["project_id"], task["id"])):
                other = json.loads(row["body"])
                if any(d["role"] == role and d["status"] in ("ACTIVE", "BLOCKED") for d in other["dispatches"]):
                    return True
        return False

    def _issue(self, task, kind, step=None, attempt=1, role=None):
        now = time.time()
        if role is None:
            if kind in ("CODE", "FIX", "ANALYZE") and step and step.get("assignee"):
                role = step["assignee"]
            else:
                role = "antigravity" if kind in ("PLAN_REVIEW", "VERIFY") else "codex"
        payload = {"requirement": task["requirement"], "draft_plan": task["draft_plan"],
                   "requirement_revision": task.get("revision", 1),
                   "plan_review": task["plan_review"], "step": step}
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
            for kind, step in candidates:
                role = (step.get("assignee") if step and kind in ("CODE", "FIX", "ANALYZE") else None) or ("antigravity" if kind in ("PLAN_REVIEW", "VERIFY") else "codex")
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
                        if a.ready]
                self._save(db, t, "DISPATCHED", {"dispatch_id": d["id"], "kind": kind})
                return {"dispatch": d}
            return {"dispatch": None}

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
            elif kind == "CHAT":
                require(data == {} or (isinstance(data, dict) and set(data) == {"proposals"}),
                        "Chat data must be empty or contain proposals")
                if data:
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
                m["replies"].append({"role": d["role"], "content": report["summary"], "report": report})
                target_count = len(m.get("participants") or
                                   (["codex", "antigravity"] if m["recipient"] in ("both", "all") else [m["recipient"]]))

                m["status"] = "BLOCKED" if report["status"] == "BLOCKED" else (
                    "DELIVERED" if len(m["replies"]) >= target_count else "QUEUED")
                db.execute("UPDATE messages SET body=? WHERE task_id=? AND id=?", (encode(m), task, m["id"]))
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
                agents = {a.id: a for a in (AgentConfig(json.loads(r[0]))
                         for r in db.execute("SELECT body FROM agents"))}
                for planned in data["steps"]:
                    kind = planned.get("work_kind", "code")
                    assignee = planned.get("assignee", "codex")
                    require(kind != "code" or assignee == "codex",
                            "Only Codex can own code steps in this version", "INVALID_PLAN")
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
            step = next((s for s in t["steps"] if s["id"] == d["step_id"]), None)
            if step:
                step["status"] = {"CODE": "CODING", "ANALYZE": "ANALYZING", "FIX": "FIXING", "VERIFY": "VERIFYING"}[d["kind"]]
            new = self._issue(t, d["kind"], step, d["attempt"] + 1)
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
