import argparse
import json
import sqlite3
import sys
from pathlib import Path

from .adapters import ManualAdapter, MockAdapter
from .core import CollabError, Engine
from .runtime import Runtime
from .antigravity import AntigravityRuntime


class Parser(argparse.ArgumentParser):
    def error(self, message):
        raise CollabError("INVALID_INPUT", message)


def parser():
    p = Parser(description="WorkTogether local collaboration core")
    p.add_argument("--db", default=".collab/state.sqlite3")
    sub = p.add_subparsers(dest="command", required=True)
    a = sub.add_parser("project-add")
    a.add_argument("--name", required=True)
    a.add_argument("--path", required=True)
    a = sub.add_parser("task-add")
    for key in ("project", "title", "requirement"):
        a.add_argument("--" + key, required=True)
    for cmd in ("show", "dispatch", "submit", "pause", "resume", "retry", "expire", "template", "handoff"):
        a = sub.add_parser(cmd)
        a.add_argument("--task", required=True)
        if cmd == "submit":
            a.add_argument("--file", required=True)
        if cmd in ("retry", "template", "handoff"):
            a.add_argument("--dispatch", required=True)
    a = sub.add_parser("doctor")
    a.add_argument("--project", required=True)
    a = sub.add_parser("revise")
    a.add_argument("--task", required=True)
    a.add_argument("--message-id", required=True)
    a.add_argument("--expected-revision", type=int, required=True)
    a.add_argument("--requirement", required=True)
    sub.add_parser("demo")
    sub.add_parser("isolation-check", help="Offline macOS isolation canary; never calls Agents")
    a = sub.add_parser("isolated-python", help="Run an explicit Python file in a disposable offline snapshot")
    a.add_argument("--project-path", required=True)
    a.add_argument("--file", action="append", required=True, dest="files")
    a.add_argument("--entry", required=True)
    a.add_argument("--timeout", type=float, default=10)
    a = sub.add_parser("run")
    a.add_argument("--task", required=True)
    a.add_argument("--dispatch", required=True)
    a.add_argument("--timeout", type=float, default=300)
    a.add_argument("--provider", choices=["codex", "antigravity", "deepseek-harness"], default="codex")
    a.add_argument("--executable", help="Explicit local provider CLI path")
    a.add_argument("--model", help="AGY model ID verified with its models command")
    a = sub.add_parser("discuss", help="At most three planning turns; never starts coding")
    a.add_argument("--task", required=True)
    a.add_argument("--codex-executable", default="codex")
    a.add_argument("--antigravity-executable", default="agy")
    a.add_argument("--antigravity-model")
    a.add_argument("--timeout", type=float, default=300)
    a = sub.add_parser("execute", help="Execute an explicitly approved plan with a bounded call budget")
    a.add_argument("--task", required=True)
    a.add_argument("--approve-digest", required=True, help="Exact digest of the reviewed plan")
    a.add_argument("--codex-executable", default="codex")
    a.add_argument("--antigravity-executable", default="agy")
    a.add_argument("--antigravity-model")
    a.add_argument("--max-calls", type=int, default=6)
    a.add_argument("--timeout", type=float, default=300)
    a = sub.add_parser("approval", help="Inspect the current plan and approval digest")
    a.add_argument("--task", required=True)
    a = sub.add_parser("runs")
    a.add_argument("--task", required=True)
    for cmd in ("collect", "resolve-stopped"):
        a = sub.add_parser(cmd)
        a.add_argument("--run", required=True)
        if cmd == "resolve-stopped":
            a.add_argument("--confirmed-stopped", action="store_true")
    a = sub.add_parser("web")
    a.add_argument("--codex-executable", default="codex")
    a.add_argument("--antigravity-executable", default="agy")
    a.add_argument("--antigravity-model", default="gemini-3.8-flash-high")
    a.add_argument("--port", type=int, default=8765)
    a.add_argument("--host", default="127.0.0.1")
    a.add_argument("--static", default=None)
    # SUPPRESS keeps the subparser from overwriting a global --db that the user
    # already supplied before the subcommand. With a plain default, argparse
    # copies the subnamespace back and silently replaces the chosen database
    # with .collab/state.sqlite3.
    a.add_argument("--db", default=argparse.SUPPRESS)
    return p


def run(args):
    db_path = getattr(args, "db", None) or ".collab/state.sqlite3"
    cmd = args.command
    # Isolation checks must not create/migrate the user's database or register
    # Agents as a side effect. The db path only excludes state from snapshots.
    if cmd == "isolation-check":
        from .isolation import check_isolation
        return check_isolation()
    if cmd == "isolated-python":
        from .isolation import run_isolated_python
        return run_isolated_python(args.project_path, args.files, args.entry,
                                   state_db=db_path, timeout=args.timeout)
    engine = Engine(db_path)
    if cmd == "approval":
        return Runtime(engine).approval(args.task)
    if cmd == "execute":
        from .execution import execute
        from .core import require
        require(1 <= args.max_calls <= 30 and 0 < args.timeout <= 1800,
                "Invalid execution budget", "INVALID_INPUT")
        codex = Runtime(engine, args.codex_executable)
        codex.require_approval = True
        codex.approval(args.task, args.approve_digest)
        return execute(engine, args.task, codex,
                       AntigravityRuntime(engine, args.antigravity_executable, args.antigravity_model),
                       max_calls=args.max_calls, timeout=args.timeout)
    if cmd == "discuss":
        from .discussion import discuss
        if engine.show(args.task).get("team"):
            from .teamwork import discuss_team as discuss
        return discuss(engine, args.task, Runtime(engine, args.codex_executable),
                        AntigravityRuntime(engine, args.antigravity_executable,
                                           args.antigravity_model), args.timeout)
    if cmd == "run":
        if args.provider == "antigravity":
            runtime = AntigravityRuntime(engine, args.executable or "agy", args.model)
        elif args.provider == "deepseek-harness":
            if args.model:
                raise CollabError("INVALID_INPUT", "Harness model selection comes from its local profile")
            from .harness import HarnessRuntime
            runtime = HarnessRuntime(engine, args.executable)
        else:
            if args.model:
                raise CollabError("INVALID_INPUT", "--model here is only supported for Antigravity")
            runtime = Runtime(engine, args.executable or "codex")
        return runtime.run(args.task, args.dispatch, args.timeout)
    if cmd == "runs":
        return Runtime(engine).list(args.task)
    if cmd == "collect":
        runtime = Runtime(engine)
        with engine.transaction() as db:
            run = runtime._get(db, args.run)
            provider = run["provider"]
            task = engine._task(db, run["task_id"])
            role = engine._get_dispatch(task, run["dispatch_id"])["role"]
        if provider == "antigravity":
            runtime = AntigravityRuntime(engine)
        elif provider == "deepseek_harness":
            from .harness import HarnessRuntime
            runtime = HarnessRuntime(engine, role=role)
        elif provider == "openai_compat":
            from .agents import AgentRegistry
            from .openai_runtime import OpenAICompatibleRuntime
            config = AgentRegistry(engine).get(role)
            if config is None:
                raise CollabError("UNSUPPORTED_PROVIDER", "Agent configuration is missing")
            runtime = OpenAICompatibleRuntime(engine, config)
        return runtime.collect(args.run)
    if cmd == "resolve-stopped":
        return Runtime(engine).resolve_stopped(args.run, args.confirmed_stopped)
    if cmd == "project-add":
        return engine.project_add(args.name, args.path)
    if cmd == "task-add":
        return engine.task_add(args.project, args.title, args.requirement)
    if cmd == "doctor":
        return engine.doctor(args.project)
    if cmd == "revise":
        return engine.revise(args.task, args.message_id, args.expected_revision, args.requirement)
    if cmd == "submit":
        report = json.loads(Path(args.file).read_text(encoding="utf-8"))
        return engine.submit(args.task, report)
    if cmd in ("retry", "template"):
        return getattr(engine, cmd)(args.task, args.dispatch)
    if cmd == "handoff":
        task = engine.show(args.task)
        dispatch = next((d for d in task["dispatches"] if d["id"] == args.dispatch), None)
        if dispatch is None:
            raise CollabError("NOT_FOUND", "Unknown dispatch")
        return ManualAdapter().prepare(dispatch, engine.template(args.task, args.dispatch))
    if cmd == "demo":
        project = engine.project_add("Simulated demo", str(Path.cwd()))
        task = engine.task_add(project["id"], "SIMULATED collaboration", "Exercise orchestration without calling models")
        for _ in range(20):
            dispatch = engine.dispatch(task["id"])["dispatch"]
            if dispatch is None:
                break
            engine.submit(task["id"], MockAdapter().prepare(dispatch, engine.template(task["id"], dispatch["id"])))
        return {"simulated": True, "task": engine.show(task["id"])}
    if cmd == "web":
        from .server import run_server
        run_server(db_path, host=args.host, port=args.port, static_dir=args.static,
                   executable=args.codex_executable, antigravity_executable=args.antigravity_executable,
                   antigravity_model=args.antigravity_model)
        return {"status": "stopped"}
    return getattr(engine, cmd)(args.task)


def main():
    try:
        args = parser().parse_args()
        result = run(args)
        print(json.dumps(result, ensure_ascii=False, allow_nan=False))
        failed = isinstance(result, dict) and (result.get("status") == "FAILED" or (
            args.command in ("isolation-check", "isolated-python") and result.get("status") == "BLOCKED"))
        return 3 if failed else 0
    except CollabError as exc:
        error = {"code": exc.code, "message": str(exc)}
    except (OSError, ValueError, sqlite3.Error) as exc:
        error = {"code": "INVALID_INPUT", "message": str(exc)}
    print(json.dumps({"error": error}, ensure_ascii=False))
    return 2


if __name__ == "__main__":
    sys.exit(main())
