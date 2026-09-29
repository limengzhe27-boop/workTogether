"""Provider boundary. Manual and simulated adapters do not invoke external models."""
from typing import Protocol


class Adapter(Protocol):
    def prepare(self, dispatch: dict, report_template: dict) -> dict:
        """Return a handoff package; no provider output may directly mutate state."""
        ...


class ManualAdapter:
    def prepare(self, dispatch, report_template):
        return {"mode": "manual", "dispatch": dispatch, "report_template": report_template,
                "instruction": "Give this task-scoped package to the named agent, then submit its JSON report."}


class MockAdapter:
    """Deterministic fixtures only; results explicitly carry simulated evidence."""
    def prepare(self, dispatch, report_template):
        report = dict(report_template)
        report.update(summary="SIMULATED result", basis=["SIMULATED adapter; no real test executed"],
                      next_action="Advance simulated workflow")
        kind = dispatch["kind"]
        if kind in ("PLAN", "PLAN_FINALIZE"):
            report["data"] = {"steps": [{"id": "s1", "title": "Simulated implementation",
                                          "acceptance": ["Simulated behavior"], "depends_on": []}]}
        elif kind in ("CODE", "FIX"):
            report["data"] = {"version": "simulated-" + dispatch["id"], "changes": ["SIMULATED change"], "known_limits": ["No code was executed"]}
        elif kind == "ANALYZE":
            report["data"] = {"deliverable": "SIMULATED analysis", "evidence": ["SIMULATED observation only"]}
        elif kind == "VERIFY":
            report.update(status="PASS", data={"version": dispatch["version"], "checks": [
                {"criterion": c, "status": "PASS", "evidence": ["SIMULATED evidence, not a real verification"]}
                for c in dispatch["payload"]["step"]["acceptance"]], "findings": []})
        return report
