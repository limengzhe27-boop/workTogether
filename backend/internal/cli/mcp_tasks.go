package cli

import (
	"context"
	"encoding/json"
	"fmt"
	"github.com/aoagents/agent-orchestrator/backend/internal/domain"
	"net/url"
	"os"
	"slices"
	"strings"
)

var peerTasksTool = map[string]any{"name": "peer_tasks", "description": "Read team tasks and revisions. Choose work suited to your abilities; claim before editing. Submitted work still needs independent review; acceptance does not merge code.", "inputSchema": map[string]any{"type": "object", "properties": map[string]any{}, "additionalProperties": false}}
var peerTaskTool = map[string]any{"name": "peer_task", "description": "Propose a task with goal and revision zero (read existing tasks to avoid duplicates), claim an open task, submit your work with evidence and candidate commit, or independently accept/reject a submission. Use the last observed revision. Read again on conflict; never steal a claim. Only edit after claim succeeds.", "inputSchema": map[string]any{"type": "object", "additionalProperties": false, "required": []string{"id", "revision", "action"}, "properties": map[string]any{"id": map[string]any{"type": "string"}, "revision": map[string]any{"type": "integer"}, "action": map[string]any{"type": "string", "enum": []string{"propose", "claim", "submit", "accept", "reject"}}, "goal": map[string]any{"type": "string"}, "evidence": map[string]any{"type": "string"}, "commit": map[string]any{"type": "string"}}}}

func init() {
	props := peerTaskTool["inputSchema"].(map[string]any)["properties"].(map[string]any)
	props["files"] = map[string]any{"type": "array", "maxItems": 100, "items": map[string]any{"type": "string"}}
	props["executions"] = map[string]any{"type": "array", "maxItems": 20, "description": "Exact room delivery IDs for your own native execution evidence. Do not invent IDs.", "items": map[string]any{"type": "string"}}
	props["tests"] = map[string]any{"type": "array", "maxItems": 30, "description": "Self-reported tests: distinguish independent reruns from unverified checks in scope.", "items": map[string]any{"type": "object", "additionalProperties": false, "required": []string{"command", "cwd", "exitCode", "scope"}, "properties": map[string]any{"command": map[string]any{"type": "string"}, "cwd": map[string]any{"type": "string"}, "exitCode": map[string]any{"type": "integer"}, "scope": map[string]any{"type": "string"}}}}
}

func (c *commandContext) callPeerTasks(ctx context.Context, raw json.RawMessage, act bool) map[string]any {
	actor := os.Getenv("AO_SESSION_ID")
	binding, err := c.peerBinding(ctx)
	if err != nil {
		return mcpToolError(err)
	}
	room, epoch := binding.RoomID, binding.Epoch
	path := "peer-rooms/" + url.PathEscape(room)
	var r domain.PeerRoom
	if err = c.getJSON(ctx, path, &r); err != nil {
		return mcpToolError(err)
	}
	if r.ProjectID != binding.ProjectID || r.Paused || r.Epoch != epoch || !slices.Contains(r.Members, actor) || r.MemberSettings[actor].Disabled {
		return mcpToolError(fmt.Errorf("room paused, revoked, disabled or non-member"))
	}
	var result any
	if act {
		var input struct {
			Files      []string                  `json:"files"`
			Tests      []domain.PeerTestEvidence `json:"tests"`
			Executions []string                  `json:"executions"`
			Goal       string                    `json:"goal"`
			ID         string                    `json:"id"`
			Revision   int64                     `json:"revision"`
			Action     string                    `json:"action"`
			Evidence   string                    `json:"evidence"`
			Commit     string                    `json:"commit"`
		}
		d := json.NewDecoder(strings.NewReader(string(raw)))
		d.DisallowUnknownFields()
		if err = d.Decode(&input); err != nil {
			return mcpToolError(err)
		}
		a := domain.PeerTaskAction{Files: input.Files, Tests: input.Tests, Executions: input.Executions, Goal: input.Goal, ID: input.ID, Actor: actor, Epoch: epoch, Revision: input.Revision, Action: input.Action, Evidence: input.Evidence, Commit: input.Commit}
		err = c.postJSON(ctx, path+"/tasks", a, &result)
	} else {
		err = c.getJSON(ctx, path+"/tasks", &result)
	}
	if err != nil {
		return mcpToolError(err)
	}
	if !act {
		result = map[string]any{"room": r, "tasks": result}
	}
	data, _ := json.Marshal(result)
	return map[string]any{"content": []any{mcpText(string(data))}}
}
