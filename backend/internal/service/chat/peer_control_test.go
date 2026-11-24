package chat

import (
	"context"
	"errors"
	"github.com/aoagents/agent-orchestrator/backend/internal/domain"
	"github.com/aoagents/agent-orchestrator/backend/internal/peerpolicy"
	"github.com/aoagents/agent-orchestrator/backend/internal/ports"
	"testing"
	"time"
)

type peerControlStore struct {
	Store
	room     domain.PeerRoom
	run      domain.PeerExecution
	resolved string
}

func (s *peerControlStore) GetPeerRoom(context.Context, string) (domain.PeerRoom, error) {
	return s.room, nil
}
func (s *peerControlStore) LoadPeerExecution(context.Context, string, string) (domain.PeerExecution, error) {
	return s.run, nil
}
func (s *peerControlStore) GetSession(context.Context, domain.SessionID) (domain.SessionRecord, bool, error) {
	return domain.SessionRecord{ProjectID: "p", Mode: domain.SessionModeChat}, true, nil
}
func (s *peerControlStore) ResolveApproval(_ context.Context, _, id, _ string, _ time.Time) error {
	s.resolved = id
	return nil
}
func (s *peerControlStore) HasPendingConversationInteractions(context.Context, string) (bool, error) {
	return true, nil
}

func (s *peerControlStore) ReservePeerControl(context.Context, string, string, string, string, string, int64) (bool, string, error) {
	return true, "", nil
}
func (s *peerControlStore) FinishPeerControl(context.Context, string, string, string, string) error {
	return nil
}

type peerControlProvider struct {
	ports.ChatConversation
	stops     []string
	decisions []string
}

func (p *peerControlProvider) Interrupt(_ context.Context, id string) error {
	p.stops = append(p.stops, id)
	return nil
}
func (p *peerControlProvider) ResolveRequest(_ context.Context, id string, d ports.ChatDecision) error {
	p.decisions = append(p.decisions, id+":"+d.ID)
	return nil
}
func controlFixture() (*Service, *peerControlStore, *peerControlProvider, *peerControlProvider) {
	st := &peerControlStore{room: domain.PeerRoom{ProjectID: "p", Epoch: 3, Members: []string{"a", "b"}}, run: domain.PeerExecution{SessionID: "a", Epoch: 3, ConversationID: "conv-a", TurnID: "turn-a", ProviderTurnID: "provider-a", State: "running"}}
	a, b := &peerControlProvider{}, &peerControlProvider{}
	svc := &Service{store: st, sessions: st, controllers: map[domain.SessionID]*Controller{"a": {sessionID: "a", conversation: domain.ConversationRecord{ID: "conv-a"}, pendingTurnID: "provider-a", conv: a, store: st, now: time.Now}, "b": {sessionID: "b", conversation: domain.ConversationRecord{ID: "conv-b"}, pendingTurnID: "provider-b", conv: b}}}
	return svc, st, a, b
}
func TestPeerControlExactStop(t *testing.T) {
	svc, st, a, b := controlFixture()
	in := PeerControlRequest{Epoch: 3, ConversationID: "conv-a", TurnID: "turn-a", Action: "stop", ID: "click"}
	out, err := svc.ControlPeerExecution(context.Background(), "r", "d", in)
	if err != nil || out.Status != "stop_requested" || len(a.stops) != 1 || a.stops[0] != "provider-a" || len(b.stops) != 0 {
		t.Fatal(out, err, a.stops, b.stops)
	}
	svc.controllers["a"].pendingTurnID = "new-run"
	if _, err = svc.ControlPeerExecution(context.Background(), "r", "d", in); err == nil {
		t.Fatal("stopped newer run")
	}
	st.room.Epoch = 4
	if _, err = svc.ControlPeerExecution(context.Background(), "r", "d", in); err == nil {
		t.Fatal("accepted stale room")
	}
	if len(a.stops) != 1 {
		t.Fatal(a.stops)
	}
}
func TestPeerControlScopeRefusal(t *testing.T) {
	for _, change := range []func(*peerControlStore, *PeerControlRequest){
		func(s *peerControlStore, i *PeerControlRequest) { i.ConversationID = "other" },
		func(s *peerControlStore, i *PeerControlRequest) { i.TurnID = "other" },
		func(s *peerControlStore, i *PeerControlRequest) { s.room.Members = []string{"b"} },
		func(s *peerControlStore, i *PeerControlRequest) { s.run.Epoch = 2; i.Action = "steer" },
		func(s *peerControlStore, i *PeerControlRequest) { s.room.Paused = true; i.Action = "steer" },
	} {
		svc, st, a, b := controlFixture()
		in := PeerControlRequest{Epoch: 3, ConversationID: "conv-a", TurnID: "turn-a", Action: "stop", ID: "id", Text: "guidance"}
		change(st, &in)
		if _, err := svc.ControlPeerExecution(context.Background(), "r", "d", in); err == nil {
			t.Fatal("unsafe control accepted", in)
		}
		if len(a.stops)+len(b.stops) != 0 {
			t.Fatal("provider called")
		}
	}
}
func TestPausedRoomStillControlsTheExactInFlightRun(t *testing.T) {
	svc, st, a, b := controlFixture()
	st.room.Epoch++
	st.room.Paused = true
	in := PeerControlRequest{Epoch: st.room.Epoch, ConversationID: "conv-a", TurnID: "turn-a", Action: "stop", ID: "stop"}
	if out, err := svc.ControlPeerExecution(context.Background(), "r", "d", in); err != nil || out.Status != "stop_requested" {
		t.Fatal(out, err)
	}
	st.run.Activities = []domain.ConversationActivity{{Kind: "approval", Status: "pending", RequestID: "request-a", Detail: []byte(`{"decisions":[{"id":"deny"}]}`)}}
	in.Action, in.ID, in.RequestID, in.Decision = "approve", "reject-approval", "request-a", "deny"
	if out, err := svc.ControlPeerExecution(context.Background(), "r", "d", in); err != nil || out.Status != "decision_submitted" {
		t.Fatal(out, err)
	}
	if len(a.stops) != 1 || len(a.decisions) != 1 || len(b.stops) != 0 || len(b.decisions) != 0 {
		t.Fatal(a, b)
	}
}

func TestPeerApprovalExactRequestAndOfferedChoice(t *testing.T) {
	svc, st, a, _ := controlFixture()
	st.run.Activities = []domain.ConversationActivity{{Kind: "approval", Status: "pending", RequestID: "request-a", Detail: []byte(`{"decisions":[{"id":"deny","label":"拒绝"}]}`)}}
	in := PeerControlRequest{Epoch: 3, ConversationID: "conv-a", TurnID: "turn-a", Action: "approve", ID: "decision-id", RequestID: "request-a", Decision: "allow"}
	if _, err := svc.ControlPeerExecution(context.Background(), "r", "d", in); err == nil {
		t.Fatal("invented choice accepted")
	}
	in.Decision = "deny"
	in.RequestID = "request-b"
	if _, err := svc.ControlPeerExecution(context.Background(), "r", "d", in); err == nil {
		t.Fatal("other request accepted")
	}
	in.RequestID = "request-a"
	svc.controllers["a"].pendingTurnID = "later-run"
	if _, err := svc.ControlPeerExecution(context.Background(), "r", "d", in); !errors.Is(err, ErrPeerRunChanged) {
		t.Fatal("old approval applied to later run", err)
	}
	svc.controllers["a"].pendingTurnID = "provider-a"
	if _, err := svc.ControlPeerExecution(context.Background(), "r", "d", in); err != nil {
		t.Fatal(err)
	}
	if len(a.decisions) != 1 || a.decisions[0] != "request-a:deny" || st.resolved != "request-a" {
		t.Fatal(a.decisions)
	}
	st.run.Activities[0].Status = "resolved"
	if _, err := svc.ControlPeerExecution(context.Background(), "r", "d", in); err == nil {
		t.Fatal("replayed decision")
	}
}

// The embedded nil Store intentionally panics if scoped steering consumes queue
// entries or records a provider response against an unrelated turn.
type scopedSteerProvider struct {
	*peerControlProvider
	calls []string
	text  string
}

func (p *scopedSteerProvider) Capabilities() ports.ChatCapabilities {
	return ports.ChatCapabilities{ports.ChatCapabilitySteer: true}
}
func (p *scopedSteerProvider) Steer(_ context.Context, turn string, msg ports.ChatUserMessage) (ports.ChatTurnRef, error) {
	p.calls = append(p.calls, turn)
	p.text = msg.Text
	return ports.ChatTurnRef{ProviderTurnID: "wrong-provider-turn"}, nil
}
func TestScopedSteerDoesNotAbsorbQueueOrAcceptAnotherRun(t *testing.T) {
	svc, _, a, b := controlFixture()
	p := &scopedSteerProvider{peerControlProvider: a}
	c := svc.controllers["a"]
	c.conv = p
	c.ackedTurnID = "provider-a"
	ctx := peerpolicy.Admitted(context.Background(), "", "a")
	_, err := c.steerExpectedLocked(ctx, ports.ChatUserMessage{Text: "only this guidance"}, "provider-a")
	if !errors.Is(err, ErrSteerDeliveryUncertain) || len(p.calls) != 1 || p.calls[0] != "provider-a" || p.text != "only this guidance" || len(b.stops) != 0 {
		t.Fatal(err, p.calls, p.text)
	}
	_, err = c.steerExpectedLocked(ctx, ports.ChatUserMessage{Text: "stale"}, "older-turn")
	if !errors.Is(err, ErrPeerRunChanged) || len(p.calls) != 1 {
		t.Fatal(err, p.calls)
	}
}
