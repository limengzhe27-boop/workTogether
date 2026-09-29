#!/usr/bin/env python3
"""AGY protocol fixture. No external model."""
import json
import os
import sys

request = json.loads(sys.stdin.readline())
dispatch = json.loads(request["message"]["content"].split("DISPATCH:\n", 1)[1])
args = sys.argv[1:]
session = args[args.index("--conversation") + 1] if "--conversation" in args else "agy-" + dispatch["task_id"]
mode = os.environ.get("WT_AGY_FIXTURE", "ok")
report = {"schema_version": 1, "task_id": dispatch["task_id"], "dispatch_id": dispatch["id"],
          "role": "antigravity", "attempt": dispatch["attempt"], "status": "DONE",
          "summary": "Fixture review", "basis": ["Fixture only"], "suggestions": ["Check persistence"],
          "next_action": "Finalize plan", "data": {}}
print(json.dumps({"event": "init", "conversation_id": session}))
result = {"event": "result", "result": {"conversation_id": session if mode != "mismatch" else "wrong",
          "status": "SUCCESS" if mode != "waiting" else "WAITING",
          "response": json.dumps(report), "usage": {"input_tokens": 5}}}
if mode == "denied":
    print(json.dumps({'event': 'step_update', 'step_update': {'state': 'ERROR', 'tool_info': {
        'parameters': {'CommandLine': 'python3 -m unittest -v'},
        'error': {'message': 'permission check failed'}}}}))
    result['result'].update(response='', denied_actions=[{'action': 'command'}])
print(json.dumps(result))
if mode == "duplicate":
    print(json.dumps(result))
