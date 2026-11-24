package chat

import (
	"context"
	"testing"

	"github.com/aoagents/agent-orchestrator/backend/internal/domain"
	"github.com/aoagents/agent-orchestrator/backend/internal/ports"
)

type roomGuardStore struct{ Store }

func (roomGuardStore) PeerSessionBound(context.Context, string) (bool, error) { return true, nil }
func TestBoundRoomRejectsLegacyChatBeforeProviderWork(t *testing.T) {
	s := &Service{store: roomGuardStore{}}
	ctx := context.Background()
	msg := ports.ChatUserMessage{Text: "hello", ClientMessageID: "x"}
	if _, err := s.Send(ctx, "member", msg); err == nil {
		t.Fatal("send")
	}
	if _, err := s.Steer(ctx, "member", msg); err == nil {
		t.Fatal("steer")
	}
	if _, err := s.SteerOrSend(ctx, "member", msg, false); err == nil {
		t.Fatal("steer-or-send")
	}
	if _, err := s.EditMessage(ctx, "member", "turn", msg); err == nil {
		t.Fatal("edit")
	}
	if _, _, err := s.SwitchProviderAndSend(ctx, "member", domain.ProviderSwitchRequest{}, msg); err == nil {
		t.Fatal("switch")
	}
	if _, err := s.SendSideQuestion(ctx, "member", "side", msg); err == nil {
		t.Fatal("side")
	}
	c := &Controller{sessionID: "member", store: roomGuardStore{}}
	if _, err := c.Send(ctx, msg); err == nil {
		t.Fatal("controller send")
	}
	if _, err := c.Steer(ctx, msg); err == nil {
		t.Fatal("controller steer")
	}
}
