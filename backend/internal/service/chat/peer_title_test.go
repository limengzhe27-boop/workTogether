package chat_test

import (
	"context"
	"errors"
	"testing"

	"github.com/aoagents/agent-orchestrator/backend/internal/domain"
	"github.com/aoagents/agent-orchestrator/backend/internal/peerpolicy"
	"github.com/aoagents/agent-orchestrator/backend/internal/ports"
	"github.com/aoagents/agent-orchestrator/backend/internal/storage/sqlite/store"
)

func TestPeerTitleMetadataPreservesMemberAliasAndConversation(t *testing.T) {
	recorder := newHistoryRecorder()
	h := newHarnessWithConversation(t, recorder)
	ctx := peerpolicy.Admitted(context.Background(), "", string(testSession))
	// Paused allows a one-member fixture; admission is exercised separately.
	if _, err := h.st.ConfigurePeerRoom(ctx, domain.PeerRoom{ID: "room", ProjectID: string(testProject), Members: []string{string(testSession)}, Budget: 24, Paused: true}); err != nil {
		t.Fatal(err)
	}
	original := h.ctrl.ConversationID()
	for i, name := range []string{"GP1", "New Name"} {
		if _, err := h.st.RenameSession(ctx, testSession, name, h.now()); err != nil {
			t.Fatal(err)
		}
		msg := ports.ChatUserMessage{Text: "[项目群聊上下文] internal routing", ClientMessageID: name, Origin: domain.MessageOriginHuman}
		turn, err := h.svc.Send(ctx, testSession, msg)
		if err != nil {
			t.Fatal(err)
		}
		titles := recorder.setTitles()
		if len(titles) != i+1 || titles[i] != "WorkTogether · p1 · "+name {
			t.Fatal(titles)
		}
		if h.ctrl.ConversationID() != original {
			t.Fatal("new conversation on send")
		}
		rec, _, err := h.st.GetSession(ctx, testSession)
		if err != nil || rec.DisplayName != name {
			t.Fatal("alias overwritten", rec.DisplayName, err)
		}
		if _, err := h.svc.Send(ctx, testSession, msg); err != nil {
			t.Fatal(err)
		}
		if len(recorder.setTitles()) != i+1 {
			t.Fatal("duplicate dispatched")
		}
		recorder.emit(ports.ChatEvent{Kind: ports.ChatEventTurnCompleted, ProviderTurnID: turn.ProviderTurnID, TurnState: domain.TurnStateCompleted})
		h.awaitSnapshot(t, func(s store.ConversationSnapshot) bool {
			for _, tr := range s.Turns {
				if tr.ID == turn.ID {
					return tr.State == domain.TurnStateCompleted
				}
			}
			return false
		})
	}
}

func TestPeerTitleFailureDoesNotFailOrReplayAcceptedMessage(t *testing.T) {
	recorder := newHistoryRecorder()
	recorder.setTitleErr = errors.New("metadata unavailable")
	h := newHarnessWithConversation(t, recorder)
	ctx := peerpolicy.Admitted(context.Background(), "", string(testSession))
	if _, err := h.st.ConfigurePeerRoom(ctx, domain.PeerRoom{ID: "room", ProjectID: string(testProject), Members: []string{string(testSession)}, Budget: 24, Paused: true}); err != nil {
		t.Fatal(err)
	}
	msg := ports.ChatUserMessage{Text: "one new request", ClientMessageID: "only-once", Origin: domain.MessageOriginHuman}
	if _, err := h.svc.Send(ctx, testSession, msg); err != nil {
		t.Fatal(err)
	}
	if _, err := h.svc.Send(ctx, testSession, msg); err != nil {
		t.Fatal(err)
	}
	s := h.awaitSnapshot(t, func(s store.ConversationSnapshot) bool { return len(s.Turns) == 1 })
	if len(s.Messages) != 1 {
		t.Fatal("replayed", s.Messages)
	}
}
