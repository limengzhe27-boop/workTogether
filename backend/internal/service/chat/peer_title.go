package chat

import (
	"context"
	"fmt"
	"strings"
	"time"

	"github.com/aoagents/agent-orchestrator/backend/internal/domain"
	"github.com/aoagents/agent-orchestrator/backend/internal/ports"
)

func (c *Controller) isPeerMember(ctx context.Context) (bool, error) {
	s, ok := c.store.(interface {
		PeerSessionBound(context.Context, string) (bool, error)
	})
	if !ok {
		return false, nil
	}
	return s.PeerSessionBound(ctx, string(c.sessionID))
}

// Use the provider's metadata operation, never a model turn or a replacement
// thread. Run after acceptance because fresh Codex threads need a materialized
// rollout. A metadata failure must never turn an accepted message into a retry.
func (c *Controller) syncPeerTitle(ctx context.Context) {
	renamer, ok := c.conv.(ports.ChatRenamer)
	if !ok {
		return
	}
	ctx, cancel := context.WithTimeout(ctx, 3*time.Second)
	defer cancel()
	bound, err := c.isPeerMember(ctx)
	if err != nil {
		c.log.Warn("cannot check peer title membership", "session", c.sessionID)
		return
	}
	if !bound {
		return
	}
	reader, ok := c.store.(interface {
		GetSession(context.Context, domain.SessionID) (domain.SessionRecord, bool, error)
	})
	if !ok {
		return
	}
	rec, found, err := reader.GetSession(ctx, c.sessionID)
	if err != nil || !found {
		return
	}
	name := strings.TrimSpace(rec.DisplayName)
	if name == "" {
		name = string(rec.ID)
	}
	title := NormalizeTitle(fmt.Sprintf("WorkTogether · %s · %s", rec.ProjectID, name))
	if err := renamer.SetTitle(ctx, title); err != nil {
		c.log.Warn("peer title sync failed; message will not be replayed", "session", c.sessionID)
		return
	}
	if err := c.applyThreadTitle(ctx, title, c.now()); err != nil {
		c.log.Warn("peer title projection failed", "session", c.sessionID)
	}
}
