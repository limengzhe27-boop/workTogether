"""Bounded planning exchange; deliberately never launches implementation."""
from .core import require


def discuss(engine, task_id, codex, antigravity, timeout=300):
    runs = []
    for _ in range(3):
        task = engine.show(task_id)
        if task["phase"] not in ("PLANNING", "PLAN_REVIEW", "PLAN_FINALIZE"):
            return {"status": "STOPPED", "reason": "PLANNING_FINISHED", "runs": runs}
        require(task["status"] == "ACTIVE", "Task is paused or blocked", "INVALID_STATE")
        # An in-flight item prevents new dispatch. Runtime also refuses a
        # second launch, including after an ambiguous process crash.
        dispatch = engine.dispatch(task_id)["dispatch"]
        if dispatch is None and any(m["status"] in ("QUEUED", "RUNNING") for m in engine.messages(task_id)):
            return {"status": "STOPPED", "reason": "MESSAGE_PENDING", "runs": runs}
        require(dispatch is not None and dispatch["kind"] in
                ("PLAN", "PLAN_REVIEW", "PLAN_FINALIZE"),
                "No planning item available", "INVALID_STATE")
        runtime = codex if dispatch["role"] == "codex" else antigravity
        result = runtime.run(task_id, dispatch["id"], timeout)
        runs.append(result)
        if result["status"] != "COMPLETED":
            return {"status": "FAILED", "reason": "PROVIDER_FAILED", "runs": runs}
    return {"status": "STOPPED", "reason": "TURN_LIMIT", "runs": runs}
