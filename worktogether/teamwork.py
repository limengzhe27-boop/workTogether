"""Bounded team planning: parallel opinions, a draft, claims, review, finalization."""
from concurrent.futures import ThreadPoolExecutor
from .core import require


def discuss_team(engine, task_id, codex, antigravity, timeout=300):
    from .agents import AgentRegistry
    registry = AgentRegistry(engine)
    providers = {"codex": codex, "antigravity": antigravity}
    runs = []

    def run(dispatch):
        provider = providers.get(dispatch["role"]) or registry.get_runtime(dispatch["role"])
        return provider.run(task_id, dispatch["id"], timeout)

    def stop(reason):
        return {"status": "STOPPED", "reason": reason, "runs": runs}

    # At most two team rounds and three planning calls. Resuming after a
    # completed round reuses its durable reports, never launches it twice.
    for _ in range(5):
        task = engine.show(task_id)
        if task["status"] != "ACTIVE":
            return stop("TASK_NOT_ACTIVE")
        if task["phase"] not in ("PLANNING", "PLAN_REVIEW", "PLAN_FINALIZE"):
            return stop("PLANNING_FINISHED")
        if any(d["status"] == "ACTIVE" for d in task["dispatches"]):
            return stop("EXISTING_DISPATCH")
        if any(m["status"] in ("QUEUED", "RUNNING") for m in engine.messages(task_id)):
            return stop("MESSAGE_PENDING")
        round_kind = {"PLANNING": "CONSULT", "PLAN_REVIEW": "CLAIM"}.get(task["phase"])
        previous = [d for d in task["dispatches"] if d["kind"] == round_kind
                    and d["payload"]["requirement_revision"] == task.get("revision", 1)]
        if round_kind and not previous:
            dispatches = engine.team_round(task_id, round_kind)
            # Read-only work can overlap. Never share a task/provider session
            # concurrently: each selected member has exactly one dispatch.
            with ThreadPoolExecutor(max_workers=min(4, len(dispatches))) as pool:
                results = list(pool.map(run, dispatches))
            runs.extend(results)
        else:
            if round_kind and not all(d["status"] in ("DONE", "SUPERSEDED") for d in previous):
                return stop("UNRESOLVED_TEAM_ROUND")
            dispatch = engine.dispatch(task_id)["dispatch"]
            require(dispatch is not None, "No planning dispatch available", "INVALID_STATE")
            results = [run(dispatch)]
            runs.extend(results)
        if any(result["status"] != "COMPLETED" for result in results):
            return {"status": "FAILED", "reason": "PROVIDER_FAILED", "runs": runs}
    return stop("PLANNING_FINISHED" if engine.show(task_id)["phase"] == "EXECUTING" else "TURN_LIMIT")
