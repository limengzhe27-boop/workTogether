#!/usr/bin/env python3
"""Harness event protocol fixture. No external model or filesystem tools."""
import json
import os
import sys
from pathlib import Path

args = sys.argv[1:]
dispatch = json.loads(sys.stdin.read().split("DISPATCH:\n", 1)[1])
policy = json.loads(Path(args[args.index("--patch") + 1]).read_text())
mode = policy[0]["config"]["mode"]
session = args[args.index("--session-id") + 1] if "--session-id" in args else (
    "harness-" + dispatch["task_id"] + "-" + mode)
fixture = os.environ.get("WT_HARNESS_FIXTURE", "ok")
report = {"schema_version": 1, "task_id": dispatch["task_id"], "dispatch_id": dispatch["id"],
          "role": dispatch["role"], "attempt": dispatch["attempt"], "status": "DONE",
          "summary": "Harness fixture only", "basis": ["Simulated protocol"], "suggestions": [],
          "next_action": "Continue fixture", "data": {}}
if dispatch["kind"] in ("PLAN", "PLAN_FINALIZE"):
    report["data"] = {"steps": [{"id": "s1", "title": "Fixture", "acceptance": ["Works"], "depends_on": [],
                                "assignee": "deepseek-harness", "verifier": "codex"}]}
elif dispatch["kind"] == "CLAIM":
    report["data"] = {"claims": [{"step_id": s["id"], "reason": "Fixture volunteer"}
                               for s in dispatch["payload"]["draft_plan"]["steps"]]}
elif dispatch["kind"] in ("CODE", "FIX"):
    report["data"] = {"version": "fixture-" + dispatch["id"], "changes": ["Fixture change"], "known_limits": []}
elif dispatch["kind"] == "ANALYZE":
    report["data"] = {"deliverable": "Fixture analysis", "evidence": ["Fake evidence"]}
elif dispatch["kind"] == "VERIFY":
    report.update(status="PASS", data={"version": dispatch["version"], "checks": [
        {"criterion": c, "status": "PASS", "evidence": ["Fixture only"]}
        for c in dispatch["payload"]["step"]["acceptance"]], "findings": []})

print(json.dumps({"type": "session", "sessionId": session, "cwd": "/wrong" if fixture == "wrong-cwd" else os.getcwd()}))
usage = {} if fixture == "missing-usage" else {"usage": {"inputTokens": 7, "outputTokens": 3}}
print(json.dumps({"type": "status", "phase": "step_end", **usage}))
print(json.dumps({"type": "status", "phase": "turn_end", "reason": {"kind": "error" if fixture == "error" else "completed"}}))
if fixture != "missing-final":
    print(json.dumps({"type": "final", "text": json.dumps(report)}))
if fixture == "duplicate":
    print(json.dumps({"type": "final", "text": json.dumps(report)}))
