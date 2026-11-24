package cli

import (
	"context"
	"encoding/json"
	"fmt"
	"github.com/aoagents/agent-orchestrator/backend/internal/domain"
	"net/url"
	"os"
	"strings"
)

var peerTool = map[string]any{
	"name": "peer_send", "description": "Send a bounded message to another active member of this project's team. This starts a model turn and consumes account usage. Do not create reply loops. A receipt is not task completion. On failure do not automatically retry.",
	"inputSchema": map[string]any{"type": "object", "additionalProperties": false, "properties": map[string]any{"roundId": map[string]any{"type": "string", "description": "Copy roundId from the triggering group/peer message. Never substitute another round."}, "id": map[string]any{"type": "string", "description": "Stable unique delivery id; reuse only to check an identical request, never auto-replay failures."}, "session": map[string]any{"type": "string"}, "message": map[string]any{"type": "string", "maxLength": 2000}}, "required": []string{"id", "session", "message"}},
}

func (c *commandContext) callPeerSend(ctx context.Context, raw json.RawMessage) map[string]any {
	sender := os.Getenv("AO_SESSION_ID")
	binding, err := c.peerBinding(ctx)
	if err != nil {
		return mcpToolError(err)
	}
	var args struct {
		RoundID string `json:"roundId"`
		ID      string `json:"id"`
		Session string `json:"session"`
		Message string `json:"message"`
	}
	decoder := json.NewDecoder(strings.NewReader(string(raw)))
	decoder.DisallowUnknownFields()
	if err := decoder.Decode(&args); err != nil {
		return mcpToolError(err)
	}
	if args.Session == "" || args.Session == sender || len(args.Message) > 2000 || strings.TrimSpace(args.Message) == "" {
		return mcpToolError(fmt.Errorf("invalid peer or message (maximum 2000 bytes; no self-send)"))
	}

	room, epoch := binding.RoomID, binding.Epoch
	if args.ID == "" {
		return mcpToolError(fmt.Errorf("delivery id required"))
	}
	msg := domain.PeerMessage{RoundID: args.RoundID, Sender: sender, Target: args.Session, ID: args.ID, Epoch: epoch, Message: args.Message}
	if err = c.postJSON(ctx, "peer-rooms/"+url.PathEscape(room)+"/send", msg, nil); err != nil {
		return mcpToolError(fmt.Errorf("delivery rejected or uncertain; do not automatically replay: %w", err))
	}
	return map[string]any{"content": []any{mcpText("Accepted or previously accepted. Recipient completion is not confirmed.")}}
}
