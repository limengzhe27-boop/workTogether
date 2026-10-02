"""Explicit, bounded task delegation from discussion through independent review."""
import json
from .core import require, revision_dispatches
from .jobs import PlanningJobs
from .runtime import Runtime
from .teamwork import discuss_team
from .execution import execute


class TeamJobs(PlanningJobs):
    table = "team_jobs"
    phases = ("PLANNING",)

    def job_fields(self, task):
        require(task.get("team") and not revision_dispatches(task),
                "Automatic delegation requires a configured, unstarted team task", "INVALID_STATE")
        # Fixed, visible budget includes every consultation/claim and model call.
        return {"max_calls": 2 * len(task["team"]["members"]) + 15,
                "authorization": {"revision": task.get("revision", 1),
                                  "requirement": task["requirement"], "team": task["team"]}}

    def perform_job(self, job, timeout):
        task_id = job["task_id"]
        planning = discuss_team(self.engine, task_id, self.codex, self.antigravity, timeout)
        if planning.get("reason") != "PLANNING_FINISHED":
            return planning
        remaining = job["max_calls"] - len(planning["runs"])
        if remaining <= 0:
            return {"status": "STOPPED", "reason": "CALL_LIMIT", "runs": planning["runs"]}
        with self.engine.transaction() as db:
            task = self.engine._task(db, task_id)
            expected = job["authorization"]
            require(task.get("revision", 1) == expected["revision"]
                    and task["requirement"] == expected["requirement"] and task.get("team") == expected["team"],
                    "Task scope changed since delegation", "APPROVAL_REQUIRED")
            if task["status"] != "ACTIVE":
                return {"status": "STOPPED", "reason": "TASK_NOT_ACTIVE", "runs": planning["runs"]}
            require(task["phase"] == "EXECUTING", "Finalized plan required", "INVALID_STATE")
            if any(json.loads(row[0])["status"] in ("QUEUED", "RUNNING") for row in db.execute(
                    "SELECT body FROM messages WHERE task_id=?", (task_id,))):
                return {"status": "STOPPED", "reason": "MESSAGE_PENDING", "runs": planning["runs"]}
            digest = Runtime.plan_digest(task)
            db.execute("INSERT OR REPLACE INTO approvals VALUES(?,?)", (task_id, digest))
            self.engine._save(db, task, "DELEGATED_PLAN_APPROVED", {"job_id": job["id"], "digest": digest,
                              "authorization": expected, "max_calls": job["max_calls"]})
        result = execute(self.engine, task_id, self.codex, self.antigravity,
                         max_calls=min(remaining, 30), timeout=timeout)
        result["runs"] = planning["runs"] + result["runs"]
        return result
