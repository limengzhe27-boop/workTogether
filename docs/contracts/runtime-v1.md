# Runtime contract v1 (additive to core v1)

Providers: `codex` and `antigravity`. Role must match provider; no silent fallback.
All commands use the same `--db PATH` as the core.

- `run --provider antigravity --executable /absolute/path/to/antigravity
  --model gemini-3.8-flash-high --task ID --dispatch ID`: execute one AGY item.
  Uses official stream-json stdin/stdout, sandbox and plan mode. Plan mode is
  not a guarantee of filesystem write isolation. Only SUCCESS is collected;
  WAITING, session mismatch and invalid reports stop the task.
- `discuss --task ID --codex-executable PATH --antigravity-executable PATH
  --antigravity-model gemini-3.8-flash-high`: at most three planning calls,
  stopping before implementation. Never automatically retries a failed run.
  This CLI capability is not yet connected to the web UI.

- `run --task ID --dispatch ID --timeout 300`: explicitly execute one previously
  issued Codex dispatch. Does not dispatch or run the next stage automatically.
- `runs --task ID`: inspect durable run records and artifact paths.
- `collect --run ID`: collect EXITED run output, including recovery after a crash
  between report ingestion and marking the run complete. Idempotent when COMPLETED.
- `resolve-stopped --run ID --confirmed-stopped`: operator acknowledgement that
  an orphan PREPARED/RUNNING process and children have stopped. Blocks dispatch;
  explicit core retry is then required. Never use merely because a PID is absent
  without assessing descendants/remote work.

CLI exit 0 for success, 2 for input/domain rejection, 3 for an execution record
with status FAILED. FAILED stdout contains the run record and error, not the
core error envelope. Default timeout 300s, bounded by dispatch deadline and 1800s.

Run: id, task_id, dispatch_id (unique), provider, status, cwd, artifact_dir,
session_id, pid, created_at, updated_at, timeout, exit_code, error, usage.
States: PREPARED → RUNNING → EXITED → COMPLETED or FAILED.

Reservation is durable before spawn. Spawn and PID registration hold the database
transaction to serialize with pause. A hard crash during spawn can still leave
PREPARED with a live child: inspect manually, never auto-replay it.
Core submit/retry reject PREPARED/RUNNING dispatches; timeout state changes alone
do not authorize running a second process.

Artifacts beside database: `runs/RUN_ID/{prompt.txt,command.json,events.jsonl,
stderr.log,result.json}`. These contain project context; do not publish them.
Model output is data, never shell-evaluated. CLI subprocess gets a bounded timeout
and new process group. Timeout sends TERM, then KILL if needed. v1 runner targets
macOS/Linux; Windows process group support is not yet implemented.

Codex PLAN/PLAN_FINALIZE use read-only sandbox; CODE/FIX use workspace-write.
No sandbox bypass. Original user/project configuration is retained.
New sessions bind to (task_id,role,provider), never selected by --last. Only sessions
created by this runtime are resumed. Existing desktop conversations are not attached.
Sessions may only belong to one task; resumed stream IDs must match the binding.

Success requires exit 0, completion event, session identity, valid JSON final
report and core contract acceptance. Missing output, malformed JSON, provider
error events and nonzero exit do not advance the task. JSON Schema enforcement
at the provider is not yet used; the core validates all returned fields.

`usage` preserves the provider's raw usage object. Null means
unavailable. In observed Codex resumed output counters may be cumulative; do not
sum them or label them per-call billing until their semantics are established.

Antigravity additional acceptance request: test no double launch, malformed/failed
provider exit, paused launch rejection, orphan recovery and session mismatch using
deterministic executable fixtures; real provider calls are separate integration tests.
