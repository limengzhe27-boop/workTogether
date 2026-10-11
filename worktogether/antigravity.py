"""Official AGY headless protocol adapter; local login is a separate prerequisite."""
import json
from pathlib import Path

from .core import CollabError, encode, require
from .runtime import Runtime


class AntigravityRuntime(Runtime):
    provider = "antigravity"
    role = "antigravity"

    def __init__(self, engine, executable="agy", model=None):
        super().__init__(engine, executable)
        self.model = model

    def command(self, run, dispatch):
        args = [self.executable, "--input-format", "stream-json", "--output-format", "stream-json",
                "--sandbox", "--mode", "plan", "--effort", "high",
                "--print-timeout", str(max(1, int(run["timeout"]))) + "s"]
        if run["session_id"]:
            args.extend(["--conversation", run["session_id"]])
        if self.model:
            args.extend(["--model", self.model])
        return args

    def input_bytes(self, prompt):
        return (encode({"event": "user", "message": {"content": prompt}}) + "\n").encode("utf-8")

    def _prompt(self, dispatch):
        prompt = super()._prompt(dispatch)
        if dispatch["kind"] == "VERIFY":
            return ("For file existence or content checks, use read-only file tools such as list_dir and view_file "
                    "instead of run_command. Run a command only when the acceptance criterion actually requires "
                    "executing code or tests and permission is available. If required permission is denied, "
                    "report BLOCKED; never treat a partial tool result as a passing check.\n\n" + prompt)
        if dispatch["kind"] == "CHAT":
            # This adapter runs AGY read-only in plan mode: shell commands are
            # routinely denied, while its file tools work. Say so, or the agent
            # spends the turn on `ls` and returns nothing.
            return ("This session runs you in read-only plan mode. Inspect the project with your read-only file "
                    "tools (for example list_dir and view_file) instead of shell commands such as ls or cat; those "
                    "are normally denied here. If a command is denied, carry on with what you can read and answer "
                    "from that. You cannot change files in this mode: if a change is needed, say exactly what to "
                    "change so the agent holding the write turn can make it.\n\n" + prompt)
        return prompt

    def read_result(self, root, dispatch=None):
        initial, result = None, None
        denied_commands = []
        with (root / "events.jsonl").open(encoding="utf-8") as stream:
            for line in stream:
                event = json.loads(line)
                require(isinstance(event, dict), "Invalid AGY event")
                update = event.get("step_update")
                if isinstance(update, dict) and update.get("state") == "ERROR":
                    info = update.get("tool_info") or {}
                    error = info.get("error") or {}
                    if isinstance(error, dict) and "permission" in str(error.get("message", "")).lower():
                        params = info.get("parameters") or {}
                        if isinstance(params, dict) and isinstance(params.get("CommandLine"), str):
                            denied_commands.append(params["CommandLine"][:2000])
                if event.get("event") == "init":
                    require(initial is None and result is None, "Duplicate or late AGY init", "INVALID_SESSION")
                    initial = event.get("conversation_id")
                    require(isinstance(initial, str) and bool(initial), "Missing AGY conversation ID", "INVALID_SESSION")
                elif event.get("event") == "result":
                    require(result is None, "Multiple AGY results for one dispatch", "INVALID_REPORT")
                    result = event.get("result")
                elif event.get("event") == "error":
                    require(False, "AGY reported a stream error", "PROVIDER_FAILED")
        require(isinstance(result, dict), "AGY did not return a terminal result", "PROVIDER_FAILED")
        denied = bool(result.get("denied_actions"))
        kind = (dispatch or {}).get("kind")
        usable = (isinstance(result.get("structured_output"), dict)
                  or bool(str(result.get("response") or "").strip()))
        if denied and not (kind == "CHAT" and usable):
            error = CollabError("PERMISSION_DENIED", "Antigravity permission denied: verification was not completed. "
                                "Review permissions explicitly; do not bypass or automatically retry.")
            error.details = {"denied_commands": denied_commands[:10], "retry_allowed": False}
            error.usage = result.get("usage")
            raise error
        require(result.get("status") == "SUCCESS", "AGY terminal status: " + str(result.get("status")), "PROVIDER_FAILED")
        session = result.get("conversation_id")
        require(initial is not None and initial == session, "AGY session mismatch", "INVALID_SESSION")
        report = result.get("structured_output")
        if report is None:
            require(isinstance(result.get("response"), str), "Missing AGY response")
            require(bool(result["response"].strip()),
                    "Antigravity returned no report; SUCCESS alone does not prove verification", "INVALID_REPORT")
            try:
                report = json.loads(result["response"])
            except ValueError:
                report = {"__raw_markdown__": result["response"].strip()}
        require(isinstance(report, dict), "AGY report must be JSON object")
        if denied:
            # A denied command is not a verification claim in chat, but the user
            # must still see that part of the work was blocked.
            note = ("⚠️ 有命令被权限拒绝，以下回答基于可读取的内容："
                    + "；".join(denied_commands[:3]) + "\n\n")
            if "__raw_markdown__" in report:
                report["__raw_markdown__"] = note + report["__raw_markdown__"]
            else:
                report["summary"] = note + str(report.get("summary", ""))
        (root / "result.json").write_text(encode(report), encoding="utf-8")
        return session, True, report, result.get("usage")
