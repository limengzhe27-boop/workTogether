package cli

import (
	"context"
	"fmt"
	"net/url"
	"os"
	"strconv"

	"github.com/aoagents/agent-orchestrator/backend/internal/domain"
)

func (c *commandContext) peerBinding(ctx context.Context) (domain.PeerToolBinding, error) {
	if os.Getenv("AO_SESSION_ID") == "" {
		return domain.PeerToolBinding{}, fmt.Errorf("session identity required")
	}
	// Preserve explicit legacy fixed bindings; never silently upgrade their epoch.
	if room := os.Getenv("AO_PEER_ROOM"); room != "" {
		epoch, err := strconv.ParseInt(os.Getenv("AO_PEER_EPOCH"), 10, 64)
		project := os.Getenv("AO_PEER_PROJECT")
		if err != nil || epoch < 1 || project == "" {
			return domain.PeerToolBinding{}, fmt.Errorf("invalid fixed binding")
		}
		return domain.PeerToolBinding{RoomID: room, ProjectID: project, Epoch: epoch}, nil
	}
	if os.Getenv("AO_PEER_PROJECT") != "" {
		return domain.PeerToolBinding{}, fmt.Errorf("incomplete fixed binding")
	}
	var binding domain.PeerToolBinding
	err := c.getJSON(ctx, "sessions/"+url.PathEscape(os.Getenv("AO_SESSION_ID"))+"/peer-binding", &binding)
	if err == nil && (binding.RoomID == "" || binding.ProjectID == "" || binding.Epoch < 1) {
		err = fmt.Errorf("invalid tool binding")
	}
	return binding, err
}
