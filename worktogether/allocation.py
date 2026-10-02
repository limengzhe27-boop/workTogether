"""Deterministic capability gates and balanced assignment; no model calls."""
from copy import deepcopy
from .core import require


def allocate(steps, agents, members, claims=()):
    """Respect capable nominations, otherwise balance load within this plan.

    A nomination is advice, not authority to invent adapter capabilities. The
    verifier must be a different, test-capable participant. Assignments are
    returned as a new plan so a failure cannot partially mutate a saved plan.
    """
    pool = {key: agents[key] for key in members if key in agents and agents[key].ready}
    load = {key: 0 for key in pool}
    owned = {key: 0 for key in pool}
    output = deepcopy(steps)
    for step in output:
        capability = step.get("work_kind", "code")
        candidates = [key for key, agent in pool.items() if capability in agent.roles]
        nominated = step.get("assignee")
        require(bool(candidates), "No team member can perform " + capability, "INVALID_PLAN")
        require(nominated is None or nominated in candidates,
                "Nominated assignee is outside the team, unavailable or incapable", "INVALID_PLAN")
        volunteers = {claim["agent"]: claim["reason"] for claim in claims
                      if claim["step_id"] == step["id"] and claim["agent"] in candidates}
        owner = nominated or min(list(volunteers) or candidates, key=lambda key: (load[key], owned[key], key))
        reviewers = [key for key, agent in pool.items() if key != owner and "verify" in agent.roles]
        reviewer = step.get("verifier")
        require(bool(reviewers), "No independent verification-capable team member", "INVALID_PLAN")
        require(reviewer is None or reviewer in reviewers,
                "Verifier must be an independent capable team member", "INVALID_PLAN")
        reviewer = reviewer or min(reviewers, key=lambda key: (load[key], key))
        step.update(assignee=owner, verifier=reviewer,
                    assignment_reason=step.get("assignment_reason") or (
                        "Capable plan nomination" if nominated else
                        "Member claim: " + volunteers[owner] if owner in volunteers else
                        "Capability match and plan workload"))
        load[owner] += 2
        owned[owner] += 1
        load[reviewer] += 1
    return output
