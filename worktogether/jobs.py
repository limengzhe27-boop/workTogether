"""Durable, bounded web planning jobs. Unknown jobs are never auto-replayed."""
import json
import threading
import time
import fcntl
import hashlib
from pathlib import Path
from .core import encode, require, uid
from .discussion import discuss


class PlanningJobs:
    table = "planning_jobs"
    phases = ("PLANNING", "PLAN_REVIEW", "PLAN_FINALIZE")

    def __init__(self, engine, codex, antigravity):
        self.engine, self.codex, self.antigravity = engine, codex, antigravity
        with engine.transaction() as db:
            db.execute(f"CREATE TABLE IF NOT EXISTS {self.table} (id TEXT PRIMARY KEY, task_id TEXT NOT NULL REFERENCES tasks(id), body TEXT NOT NULL)")

    def list(self, task_id):
        with self.engine.transaction() as db:
            self.engine._task(db, task_id)
            return [json.loads(r[0]) for r in db.execute(
                f"SELECT body FROM {self.table} WHERE task_id=? ORDER BY rowid", (task_id,))]

    def start(self, task_id, timeout=300):
        require(type(timeout) in (int, float) and 0 < timeout <= 1800,
                "Timeout must be between 0 and 1800 seconds", "INVALID_INPUT")
        with self.engine.transaction() as db:
            task = self.engine._task(db, task_id)
            require(task["status"] == "ACTIVE" and task["phase"] in
                    self.phases,
                    "Task is not ready for planning", "INVALID_STATE")
            for row in db.execute(f"SELECT body FROM {self.table} WHERE task_id=?", (task_id,)):
                require(json.loads(row[0])["status"] not in ("QUEUED", "RUNNING"),
                        "Planning already started; inspect existing job, never replay unknown work", "ALREADY_STARTED")
            require(not any(d["status"] == "ACTIVE" for d in task["dispatches"]),
                    "An existing dispatch needs to be handled first", "INVALID_STATE")
            job = {"id": uid(), "task_id": task_id, "status": "QUEUED",
                   "created_at": time.time(), "result": None, "error": None}
            db.execute(f"INSERT INTO {self.table} VALUES(?,?,?)", (job["id"], task_id, encode(job)))
        response = dict(job)
        try:
            threading.Thread(target=self._work, args=(job, timeout), daemon=True,
                             name="Planning-" + job["id"]).start()
        except Exception as exc:
            job.update(status="FAILED", error=str(exc))
            self._save(job)
            raise
        return response

    def _save(self, job):
        job["updated_at"] = time.time()
        with self.engine.transaction() as db:
            db.execute(f"UPDATE {self.table} SET body=? WHERE id=?", (encode(job), job["id"]))

    def perform(self, task_id, timeout):
        if self.engine.show(task_id).get("team"):
            from .teamwork import discuss_team
            return discuss_team(self.engine, task_id, self.codex, self.antigravity, timeout)
        return discuss(self.engine, task_id, self.codex, self.antigravity, timeout)

    def _lease(self, job_id):
        key = hashlib.sha256((self.table + job_id).encode()).hexdigest()
        handle = (Path(self.engine.path).parent / ("job-" + key + ".lock")).open("a")
        try:
            fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            handle.close()
            require(False, "Job is still running; pause and wait for it to finish", "INVALID_STATE")
        return handle

    def recover(self, task_id, job_id):
        with self._lease(job_id):
            with self.engine.transaction() as db:
                task = self.engine._task(db, task_id)
                row = db.execute(f"SELECT body FROM {self.table} WHERE id=? AND task_id=?", (job_id, task_id)).fetchone()
                require(row is not None, "Unknown job", "NOT_FOUND")
                job = json.loads(row[0])
                if job["status"] not in ("QUEUED", "RUNNING"):
                    return job
                require(not any(d["status"] == "ACTIVE" for d in task["dispatches"]),
                        "An unresolved dispatch must be inspected first", "INVALID_STATE")
                for row in db.execute("SELECT body FROM runs WHERE task_id=?", (task_id,)):
                    require(json.loads(row[0])["status"] not in ("PREPARED", "RUNNING", "EXITED"),
                            "A model run remains unresolved; inspect it before recovery", "INVALID_STATE")
                job.update(status="INTERRUPTED", updated_at=time.time(),
                           result={"status": "STOPPED", "reason": "RECOVERED_NO_ACTIVE_RUN"})
                db.execute(f"UPDATE {self.table} SET body=? WHERE id=?", (encode(job), job_id))
                self.engine._save(db, task, "JOB_RECOVERED", {"job_id": job_id, "table": self.table})
                return job

    def _work(self, job, timeout):
        with self._lease(job["id"]):
            with self.engine.transaction() as db:
                row = db.execute(f"SELECT body FROM {self.table} WHERE id=?", (job["id"],)).fetchone()
                if row is None or json.loads(row[0])["status"] != "QUEUED":
                    return
            self._work_locked(job, timeout)

    def _work_locked(self, job, timeout):
        try:
            job["status"] = "RUNNING"
            self._save(job)
            result = self.perform(job["task_id"], timeout)
            job.update(result=result, status="COMPLETED" if result["status"] == "STOPPED" else "FAILED")
            if result.get("reason") == "MESSAGE_PENDING":
                job["status"] = "INTERRUPTED"
        except Exception as exc:
            job.update(status="FAILED", error=str(exc))
        finally:
            self._save(job)


class ExecutionJobs(PlanningJobs):
    table = "execution_jobs"
    phases = ("EXECUTING",)

    def start(self, task_id, timeout=300):
        require(self.codex.approval(task_id)["approved"],
                "Approve the current plan first", "APPROVAL_REQUIRED")
        return super().start(task_id, timeout)

    def perform(self, task_id, timeout):
        from .execution import execute
        return execute(self.engine, task_id, self.codex, self.antigravity,
                       max_calls=6, timeout=timeout)
