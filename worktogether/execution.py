"""Bounded opt-in execution; provider errors are never automatically retried."""
from .core import require
from pathlib import Path
import fcntl
import hashlib


def execute(engine, task_id, codex, antigravity, *, max_calls=6, timeout=300):
    # Local process lease; release on exit/crash. Outstanding dispatches still
    # prevent replay after a crash. Hash avoids user-controlled path segments.
    key = hashlib.sha256(task_id.encode()).hexdigest()
    lock = Path(engine.path).parent / ("execution-" + key + ".lock")
    with lock.open("a") as handle:
        try:
            fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            return {"status": "STOPPED", "reason": "EXECUTION_BUSY", "runs": []}
        try:
            return _execute(engine, task_id, codex, antigravity, max_calls=max_calls, timeout=timeout)
        finally:
            fcntl.flock(handle, fcntl.LOCK_UN)


def _execute(engine, task_id, codex, antigravity, *, max_calls, timeout):
    require(type(max_calls) is int and 1 <= max_calls <= 30,
            "max_calls must be an integer between 1 and 30", "INVALID_INPUT")
    require(type(timeout) in (int, float) and 0 < timeout <= 1800,
            "timeout must be between 0 and 1800", "INVALID_INPUT")
    runs = []

    def stop(reason):
        return {"status": "STOPPED", "reason": reason, "runs": runs}

    for _ in range(max_calls):
        task = engine.show(task_id)
        if task["status"] == "COMPLETED":
            return stop("COMPLETED")
        if task["status"] != "ACTIVE":
            return stop("TASK_NOT_ACTIVE")
        if task["phase"] != "EXECUTING":
            return stop("PLAN_REQUIRED")
        if not codex.approval(task_id)["approved"]:
            return stop("APPROVAL_REQUIRED")
        # Never collect, replay or replace another controller's work.
        if any(d["status"] == "ACTIVE" for d in task["dispatches"]):
            return stop("EXISTING_DISPATCH")
        if any(m["status"] in ("QUEUED", "RUNNING") for m in engine.messages(task_id)):
            return stop("MESSAGE_PENDING")
        dispatch = engine.dispatch(task_id)["dispatch"]
        if dispatch is None:
            return stop("NO_DISPATCH")
        require(dispatch["kind"] in ("CODE", "VERIFY", "FIX"),
                "Execution cannot run planning or chat", "INVALID_STATE")
        if dispatch["role"] == "codex":
            provider = codex
        elif dispatch["role"] == "antigravity":
            provider = antigravity
        else:
            from .agents import AgentRegistry
            provider = AgentRegistry(engine).get_runtime(dispatch["role"])
        result = provider.run(task_id, dispatch["id"], timeout)
        runs.append(result)
        if result["status"] != "COMPLETED":
            return {"status": "FAILED", "reason": "PROVIDER_FAILED", "runs": runs}
    return stop("COMPLETED" if engine.show(task_id)["status"] == "COMPLETED" else "CALL_LIMIT")
