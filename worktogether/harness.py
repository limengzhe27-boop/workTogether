"""DeepSeek Harness 0.2.0-rc.2 headless adapter, based on installed source.

No guessed Hermes protocol, permissive flags, or automatic dependency installs.
"""
import json
import shutil
from pathlib import Path
from .core import CollabError, encode, require
from .runtime import Runtime


MAC_EXECUTABLE = "/Applications/DeepSeek Harness.app/Contents/Resources/runtime/cli/bin/dsh"


def resolve_executable():
    return shutil.which("dsh") or (MAC_EXECUTABLE if Path(MAC_EXECUTABLE).is_file() else "dsh")


class HarnessRuntime(Runtime):
    provider = "deepseek_harness"
    role = "deepseek-harness"

    def __init__(self, engine, executable=None, role="deepseek-harness"):
        super().__init__(engine, executable or resolve_executable())
        self.role = role
        self.require_approval = True

    def session_scope(self, dispatch):
        # A session can persist permission overrides in Harness. Never resume a
        # writer session for read-only work, even within the same project task.
        return self.provider + (":write" if dispatch["kind"] in ("CODE", "FIX") else ":read")

    def command(self, run, dispatch):
        mode = "workspace-write" if dispatch["kind"] in ("CODE", "FIX") else "read-only"
        policy = [
            {"id": "sandbox-policy", "config": {"mode": mode, "workspaceRoot": run["cwd"]}},
            # Harness 'never' DENIES escalation; it is not auto-approval.
            {"id": "approval", "config": {"policy": "never"}},
        ]
        # Keep orchestration and call budgets outside the worker. These stable
        # plugin IDs are verified against the installed 0.2.0-rc.2 base bundle.
        for plugin in ("tool-subagent", "tool-subagent-fork", "tool-subagent-control",
                       "tool-workflow", "tool-goal", "command-goal", "goal-round-driver",
                       "tool-ralph", "tool-plugin-manager", "session-title-llm"):
            policy.append({"id": plugin, "disabled": True})
        root = Path(run["artifact_dir"])
        overlay = root / "harness-policy.json"
        overlay.write_text(encode(policy), encoding="utf-8")
        args = [self.executable, "--profile", "headless", "--patch", str(overlay), "--json"]
        if run["session_id"]:
            args.extend(["--session-id", run["session_id"]])
        return args + ["-"]

    def read_result(self, root):
        session, final, ended = None, None, False
        totals, usage_complete, steps = {}, True, 0
        expected = json.loads((root / "harness-policy.json").read_text(encoding="utf-8"))[0]["config"]["workspaceRoot"]
        with (root / "events.jsonl").open(encoding="utf-8") as stream:
            for line in stream:
                if not line.strip():
                    continue
                event = json.loads(line)
                require(isinstance(event, dict), "Invalid Harness event", "INVALID_REPORT")
                kind = event.get("type")
                if kind == "error":
                    raise CollabError("PROVIDER_FAILED", "Harness reported an error; inspect its event log")
                if kind == "session":
                    require(session is None and isinstance(event.get("sessionId"), str)
                            and event["sessionId"].strip(), "Invalid/duplicate Harness session", "INVALID_SESSION")
                    require(isinstance(event.get("cwd"), str) and Path(event["cwd"]).resolve() == Path(expected).resolve(),
                            "Harness used a different working directory", "INVALID_SESSION")
                    session = event["sessionId"]
                elif kind == "status" and event.get("phase") == "turn_end":
                    require(not ended and isinstance(event.get("reason"), dict)
                            and event["reason"].get("kind") == "completed",
                            "Harness did not finish normally", "PROVIDER_FAILED")
                    ended = True
                elif kind == "status" and event.get("phase") == "step_end":
                    steps += 1
                    usage = event.get("usage")
                    if not isinstance(usage, dict) or not all(type(usage.get(k)) is int and usage[k] >= 0
                            for k in ("inputTokens", "outputTokens")):
                        usage_complete = False
                    else:
                        for source, target in (("inputTokens", "input_tokens"), ("outputTokens", "output_tokens")):
                            totals[target] = totals.get(target, 0) + usage[source]
                elif kind == "final":
                    require(session is not None and ended and final is None and isinstance(event.get("text"), str)
                            and not event.get("truncated"), "Invalid/duplicate Harness final event", "INVALID_REPORT")
                    final = event["text"]
        require(session is not None and ended and final is not None, "Incomplete Harness stream", "INVALID_REPORT")
        report = json.loads(final)
        require(isinstance(report, dict), "Harness must return a report object", "INVALID_REPORT")
        (root / "result.json").write_text(encode(report), encoding="utf-8")
        return session, True, report, totals if usage_complete and steps else None
