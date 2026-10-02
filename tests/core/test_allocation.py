import copy
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

from worktogether.adapters import MockAdapter
from worktogether.agents import AgentRegistry
from worktogether.allocation import allocate
from worktogether.core import CollabError, Engine


def step(key="s1", **extra):
    return dict(id=key, title="Implement behavior", acceptance=["Works"], depends_on=[], **extra)


class AllocationTests(unittest.TestCase):
    def test_sole_verifier_is_reserved_even_when_it_volunteers(self):
        agents = {"alpha": SimpleNamespace(ready=True, roles=["analysis", "verify"]),
                  "beta": SimpleNamespace(ready=True, roles=["analysis"])}
        source = [step(work_kind="analysis")]
        result = allocate(source, agents, list(agents),
                          [{"agent": "alpha", "step_id": "s1", "reason": "I can analyze"}])
        self.assertEqual("beta", result[0]["assignee"])
        self.assertEqual("alpha", result[0]["verifier"])
        self.assertNotIn("assignee", source[0])

    def test_explicit_verifier_is_reserved_during_owner_selection(self):
        agents = {key: SimpleNamespace(ready=True, roles=["code", "verify"])
                  for key in ("alpha", "beta")}
        result = allocate([step(verifier="alpha")], agents, list(agents))
        self.assertEqual("beta", result[0]["assignee"])
        self.assertEqual("alpha", result[0]["verifier"])

    def test_infeasible_owner_nomination_is_not_silently_reassigned(self):
        agents = {"alpha": SimpleNamespace(ready=True, roles=["analysis", "verify"]),
                  "beta": SimpleNamespace(ready=True, roles=["analysis"])}
        with self.assertRaises(CollabError):
            allocate([step(work_kind="analysis", assignee="alpha")], agents, list(agents))

    def test_balances_capable_agents_without_brand_preference(self):
        agents = {key: SimpleNamespace(ready=True, roles=["code", "verify"])
                  for key in ("alpha", "beta", "gamma")}
        source = [step(str(i)) for i in range(6)]
        result = allocate(source, agents, list(agents))
        self.assertEqual(set(agents), {s["assignee"] for s in result})
        self.assertTrue(all(s["assignee"] != s["verifier"] for s in result))
        self.assertNotIn("assignee", source[0])
        self.assertEqual(result, allocate(source, agents, list(reversed(agents))))

    def test_nominations_cannot_escalate_or_self_verify(self):
        agents = {"writer": SimpleNamespace(ready=True, roles=["code", "verify"]),
                  "reader": SimpleNamespace(ready=True, roles=["analysis", "verify"])}
        for bad in (step(assignee="reader"), step(assignee="outsider"),
                    step(assignee="writer", verifier="writer")):
            with self.assertRaises(CollabError):
                allocate([bad], agents, list(agents))
        with self.assertRaises(CollabError):
            allocate([step()], agents, ["writer"])


class TeamEngineTests(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.engine = Engine(Path(directory.name) / "state.db")
        self.registry = AgentRegistry(self.engine)
        self.registry.register(dict(id="thinker", name="Thinker", type="openai_compat",
                                    endpoint="http://127.0.0.1:1234/v1", model="test"))
        project = self.engine.project_add("project", directory.name)
        self.task = self.engine.task_add(project["id"], "Task", "Do work")["id"]

    def plan(self, steps):
        roles = []
        for _ in range(3):
            phase = self.engine.show(self.task)["phase"]
            if phase in ("PLANNING", "PLAN_REVIEW"):
                for dispatch in self.engine.team_round(self.task, "CONSULT" if phase == "PLANNING" else "CLAIM"):
                    report = self.engine.template(self.task, dispatch["id"])
                    self.engine.submit(self.task, report)
            d = self.engine.dispatch(self.task)["dispatch"]
            roles.append(d["role"])
            report = MockAdapter().prepare(d, self.engine.template(self.task, d["id"]))
            if d["kind"] != "PLAN_REVIEW":
                report["data"] = {"steps": copy.deepcopy(steps)}
            self.engine.submit(self.task, report)
            self.assertTrue(self.engine.submit(self.task, report)["duplicate"])
        return roles

    def test_custom_coordinator_and_independent_analysis_verifier(self):
        self.engine.configure_team(self.task, ["thinker", "codex", "antigravity"], "thinker", "codex")
        self.assertEqual(["thinker", "codex", "thinker"], self.plan([
            step(work_kind="analysis", assignee="antigravity")]))
        assigned = self.engine.show(self.task)["steps"][0]
        self.assertEqual("codex", assigned["verifier"])
        d = self.engine.dispatch(self.task)["dispatch"]
        report = MockAdapter().prepare(d, self.engine.template(self.task, d["id"]))
        self.engine.submit(self.task, report)
        verification = self.engine.dispatch(self.task)["dispatch"]
        self.assertEqual("codex", verification["role"])

    def test_api_worker_and_sole_cli_verifier_can_finalize_without_nominations(self):
        self.engine.configure_team(self.task, ["thinker", "codex"], "thinker", "codex")
        self.plan([step(work_kind="analysis")])
        assigned = self.engine.show(self.task)["steps"][0]
        self.assertEqual("thinker", assigned["assignee"])
        self.assertEqual("codex", assigned["verifier"])

    def test_team_membership_is_frozen_and_broadcast_is_scoped(self):
        self.engine.configure_team(self.task, ["thinker", "codex"], "thinker", "codex")
        message = self.engine.message_add(self.task, "first", "all", "Discuss")
        self.assertEqual({"thinker", "codex"}, set(message["participants"]))
        with self.assertRaises(CollabError):
            self.engine.message_add(self.task, "second", "antigravity", "Discuss")
        with self.assertRaises(CollabError):
            self.registry.remove("thinker")

    def test_no_reconfiguration_after_dispatch(self):
        self.engine.dispatch(self.task)
        with self.assertRaises(CollabError):
            self.engine.configure_team(self.task, ["codex", "antigravity"])

    def test_api_nomination_cannot_gain_write_access(self):
        self.engine.configure_team(self.task, ["thinker", "codex", "antigravity"])
        with self.assertRaises(CollabError):
            self.plan([step(assignee="thinker")])
        self.assertEqual("PLAN_FINALIZE", self.engine.show(self.task)["phase"])

    def test_api_only_team_fails_before_spending_on_discussion(self):
        self.registry.register(dict(id="thinker-two", name="Thinker 2", type="openai_compat",
                                    endpoint="http://127.0.0.1:1234/v1", model="test"))
        with self.assertRaises(CollabError):
            self.engine.configure_team(self.task, ["thinker", "thinker-two"])
        self.assertEqual([], self.engine.show(self.task)["dispatches"])

    def test_disabled_builtin_does_not_run_in_legacy_mode(self):
        self.registry.set_enabled("codex", False)
        self.assertEqual("AGENT_UNAVAILABLE", self.engine.dispatch(self.task)["reason"])
        self.registry.set_enabled("codex", True)
        self.assertEqual("codex", self.engine.dispatch(self.task)["dispatch"]["role"])
        with self.assertRaises(CollabError):
            self.registry.set_enabled("codex", False)
