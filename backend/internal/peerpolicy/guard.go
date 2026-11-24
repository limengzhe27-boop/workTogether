// Package peerpolicy prevents legacy delivery from bypassing room admission.
package peerpolicy

import (
	"context"

	"github.com/aoagents/agent-orchestrator/backend/internal/httpd/apierr"
)

type bindingStore interface {
	PeerSessionBound(context.Context, string) (bool, error)
}
type admissionKey struct{}
type admission struct{ sender, target string }

// Admitted is used only after a durable room budget reservation. Never decode it from HTTP.
func Admitted(ctx context.Context, sender, target string) context.Context {
	return context.WithValue(ctx, admissionKey{}, admission{sender, target})
}
func Check(ctx context.Context, store any, sender, target string) error {
	if a, ok := ctx.Value(admissionKey{}).(admission); ok && a.sender == sender && a.target == target {
		return nil
	}
	s, ok := store.(bindingStore)
	if !ok {
		return nil
	} // legacy test stores; production SQLite implements this port
	for _, id := range []string{sender, target} {
		if id == "" {
			continue
		}
		bound, err := s.PeerSessionBound(ctx, id)
		if err != nil {
			return apierr.Unavailable("PEER_POLICY_UNAVAILABLE", "Cannot verify room binding")
		}
		if bound {
			return apierr.Conflict("PEER_ROOM_REQUIRED", "This session requires room admission; use the bound peer-room channel", nil)
		}
	}
	return nil
}
