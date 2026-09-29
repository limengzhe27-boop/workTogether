# Multi-Agent delegation slice

This iteration extends the existing WorkTogether state machine; it does not
replace it with a third-party framework. The design borrows three specific
ideas:

- [AutoGen SelectorGroupChat](https://microsoft.github.io/autogen/dev/user-guide/agentchat-user-guide/selector-group-chat.html): each participant sees earlier opinions, but the exchange has a hard turn limit. WorkTogether still uses an explicit, task-scoped message queue rather than an unbounded model-selected speaker loop.
- [CrewAI hierarchical process](https://github.com/crewAIInc/crewAI/blob/main/docs/v1.12.2/en/learn/hierarchical-process.mdx): a coordinator proposes a task graph and assigns capable workers. WorkTogether validates the assignment deterministically and requires the user's plan approval before execution.
- [Agent Orchestrator](https://github.com/Untrivial-ai/agent-orchestrator): separate agent adapters from task lifecycle; isolate parallel coding workers in worktrees. This iteration adopts the adapter/capability boundary only. Worktree-backed parallel code execution is a later milestone, not a claimed feature.

## Current end-to-end path

1. Agents discuss inside one task and may each include up to three structured work proposals (`title`, `work_kind`, `assignee`, `reason`). These are advice, not executable commands. Completed conversation turns and proposals from the current requirement revision enter the next `PLAN` and `PLAN_FINALIZE` dispatch alongside the ready-agent capability list.
2. The finalized plan may include a `work_kind: "analysis"` step assigned to a ready custom OpenAI-compatible Agent. Code steps remain Codex-only. Invalid or unavailable assignments are rejected atomically.
3. The user approves the finalized plan. The deterministic runner dispatches the analysis to its assigned Agent. API Agents receive a read-only instruction and cannot return a code handoff. They see the supplied task, plan, discussion and dependency excerpts, not arbitrary repository files; source-level research still needs a tool-capable adapter.
4. The scheduler computes a digest for the analysis output. Antigravity receives an independent `VERIFY` dispatch against that digest and the exact acceptance criteria. A pass unlocks dependent steps; a fail returns the analysis with findings, subject to the existing two-repair cap.
5. A dependent Codex step receives a bounded excerpt of the verified analysis rather than a full model transcript.

All reports remain task-scoped and audited. Provider errors never silently pass verification. The existing Antigravity tool-permission limitation remains unresolved; the tests for this feature use deterministic fake CLI/HTTP responses, not paid live models.
An API Agent marked ready has a complete configuration; that does not prove its endpoint is currently reachable or its credentials are valid. Those failures surface when a real dispatch runs.
For API calls, the adapter requires plan approval for analysis even when invoked directly, rechecks task/dispatch state immediately before the request, rejects HTTP redirects instead of forwarding task text or credentials, and treats malformed provider output as a failed run rather than an Agent reply.

## Explicitly not yet supported

- Claude Code, ZCode, or arbitrary CLI work adapters.
- Model-initiated changes to the approved plan or unchecked autonomous task spawning.
- Multiple concurrent code writers sharing one checkout; worktrees and merge review must precede that capability.
- A successful real DeepSeek/Antigravity end-to-end trial. The next validation should exercise one approved analysis step with the user's actual configured providers.
