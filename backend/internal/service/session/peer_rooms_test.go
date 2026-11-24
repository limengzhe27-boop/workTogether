package session

import (
	"context"
	"errors"
	"github.com/aoagents/agent-orchestrator/backend/internal/domain"
	"testing"
)

type peerStoreFake struct {
	Store
	pending, accepted bool
	reserveErr        error
}

func (p *peerStoreFake) GetPeerRoom(context.Context, string) (domain.PeerRoom, error) {
	return domain.PeerRoom{}, nil
}
func (p *peerStoreFake) ConfigurePeerRoom(_ context.Context, r domain.PeerRoom) (domain.PeerRoom, error) {
	return r, nil
}
func (p *peerStoreFake) ReservePeerMessage(context.Context, string, domain.PeerMessage) (bool, error) {
	if p.reserveErr != nil {
		return false, p.reserveErr
	}
	if p.pending {
		if p.accepted {
			return false, nil
		}
		return false, errors.New("uncertain")
	}
	p.pending = true
	return true, nil
}
func (p *peerStoreFake) FinishPeerMessage(_ context.Context, _, _ string, ok bool) error {
	p.accepted = ok
	return nil
}
func TestPeerRoomDeliveryDoesNotReplay(t *testing.T) {
	for _, failure := range []bool{false, true} {
		t.Run(map[bool]string{false: "accepted", true: "uncertain"}[failure], func(t *testing.T) {
			p := &peerStoreFake{}
			calls := 0
			m := &fakeCommander{sendFunc: func(domain.SessionID, string) error {
				calls++
				if failure {
					return errors.New("connection lost")
				}
				return nil
			}}
			s := NewWithDeps(Deps{Store: p, Manager: m})
			msg := domain.PeerMessage{ID: "x", Sender: "a", Target: "b"}
			err := s.SendPeerMessage(context.Background(), "r", msg)
			if (err != nil) != failure {
				t.Fatal(err)
			}
			s.SendPeerMessage(context.Background(), "r", msg)
			if calls != 1 {
				t.Fatal(calls)
			}
		})
	}
}
func TestPeerRoomRejectionNeverCallsAgent(t *testing.T) {
	p := &peerStoreFake{reserveErr: errors.New("paused")}
	calls := 0
	s := NewWithDeps(Deps{Store: p, Manager: &fakeCommander{sendFunc: func(domain.SessionID, string) error { calls++; return nil }}})
	if s.SendPeerMessage(context.Background(), "r", domain.PeerMessage{}) == nil || calls != 0 {
		t.Fatal("rejection failed")
	}
}

func (p *peerStoreFake) GetSession(_ context.Context, id domain.SessionID) (domain.SessionRecord, bool, error) {
	return domain.SessionRecord{ID: id}, true, nil
}

func (p *peerStoreFake) ListPeerDeliveries(context.Context, string) ([]domain.PeerDelivery, error) {
	return nil, nil
}
func (p *peerStoreFake) ListPeerTasks(context.Context, string) ([]domain.PeerTask, error) {
	return nil, nil
}
