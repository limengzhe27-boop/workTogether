#!/usr/bin/env python3
"""Subprocess fixture, never a real model."""
import json
import os
import sys
import time
from pathlib import Path

args = sys.argv[1:]
prompt = sys.stdin.read()
dispatch = json.loads(prompt.split("DISPATCH:\n", 1)[1])
mode = os.environ.get("WT_FAKE_MODE", "ok")
if mode == "timeout":
    time.sleep(10)
if mode == "exit":
    sys.exit(9)
session = args[args.index("resume") + 1] if "resume" in args else "fixture-" + dispatch["task_id"]
if mode == "wrong-session":
    session = "wrong-session"
print(json.dumps({"type": "thread.started", "thread_id": session}), flush=True)
print(json.dumps({"type": "turn.completed", "usage": {"input_tokens": 10, "output_tokens": 5}}), flush=True)
report = {"schema_version": 1, "task_id": dispatch["task_id"], "dispatch_id": dispatch["id"],
          "role": dispatch["role"], "attempt": dispatch["attempt"], "status": "DONE",
          "summary": "Fixture result", "basis": ["Fake process"], "suggestions": [],
          "next_action": "Continue fixture", "data": {"steps": [{"id": "s1", "title": "fixture",
            "acceptance": ["works"], "depends_on": []}]}}
if dispatch["kind"] == "CHAT":
    report["data"] = {}
    report["summary"] = "Fixture reply: " + dispatch["payload"]["message"]
if mode == "invalid":
    report = {}
Path(args[args.index("-o") + 1]).write_text(json.dumps(report))
