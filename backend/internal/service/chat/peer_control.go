package chat

import (
	"context"
	"crypto/sha256"
	"encoding/json"
	"errors"
	"fmt"
	"github.com/aoagents/agent-orchestrator/backend/internal/domain"
	"github.com/aoagents/agent-orchestrator/backend/internal/peerpolicy"
	"github.com/aoagents/agent-orchestrator/backend/internal/ports"
	"slices"
	"strings"
)

type PeerControlRequest struct {
	Epoch          int64  `json:"epoch"`
	ConversationID string `json:"conversationId"`
	TurnID         string `json:"turnId"`
	Action         string `json:"action"`
	ID             string `json:"id"`
	Text           string `json:"text,omitempty"`
	RequestID      string `json:"requestId,omitempty"`
	Decision       string `json:"decision,omitempty"`
}
type PeerControlResult struct {
	Status string `json:"status"`
}

var ErrPeerRunChanged = errors.New("指定运行已变化或结束；未操作其他任务")

type peerExecutionStore interface {
	GetPeerRoom(context.Context, string) (domain.PeerRoom, error)
	LoadPeerExecution(context.Context, string, string) (domain.PeerExecution, error)
	ReservePeerControl(context.Context, string, string, string, string, string, int64) (bool, string, error)
	FinishPeerControl(context.Context, string, string, string, string) error
}

func (s *Service) ControlPeerExecution(ctx context.Context, roomID, deliveryID string, in PeerControlRequest) (result PeerControlResult, resultErr error) {
	store, ok := s.store.(peerExecutionStore)
	if !ok {
		return PeerControlResult{}, fmt.Errorf("room controls unavailable")
	}
	room, err := store.GetPeerRoom(ctx, roomID)
	if err != nil {
		return PeerControlResult{}, err
	}
	run, err := store.LoadPeerExecution(ctx, roomID, deliveryID)
	if err != nil {
		return PeerControlResult{}, err
	}
	if room.Epoch != in.Epoch || !slices.Contains(room.Members, run.SessionID) || in.ConversationID != run.ConversationID || in.TurnID != run.TurnID || run.ProviderTurnID == "" {
		return PeerControlResult{}, ErrPeerRunChanged
	}
	// A room edit does not start a new native turn. The human can still stop
	// or resolve a pending request for an exact in-flight delivery.
	if in.Action == "steer" && (run.Epoch != room.Epoch || room.Paused || room.MemberSettings[run.SessionID].Disabled) {
		return PeerControlResult{}, fmt.Errorf("房间或成员已暂停，或此运行属于旧房间配置")
	}
	rec, err := s.requireChatSession(ctx, domain.SessionID(run.SessionID))
	if err != nil {
		return PeerControlResult{}, err
	}
	if string(rec.ProjectID) != room.ProjectID {
		return PeerControlResult{}, ErrPeerRunChanged
	}
	controller, err := s.Controller(domain.SessionID(run.SessionID))
	if err != nil {
		return PeerControlResult{}, err
	}
	if controller.conversation.ID != run.ConversationID {
		return PeerControlResult{}, ErrPeerRunChanged
	}

	if len(in.ID) == 0 || len(in.ID) > 100 || !slices.Contains([]string{"stop", "steer", "approve"}, in.Action) {
		return PeerControlResult{}, fmt.Errorf("操作编号或类型无效")
	}
	raw, _ := json.Marshal(in)
	digest := fmt.Sprintf("%x", sha256.Sum256(raw))
	admitted, prior, err := store.ReservePeerControl(ctx, roomID, deliveryID, in.ID, digest, in.Action, in.Epoch)
	if err != nil {
		return PeerControlResult{}, err
	}
	if !admitted {
		return PeerControlResult{Status: prior}, nil
	}
	defer func() {
		status := result.Status
		if resultErr != nil || status == "" {
			status = "uncertain"
		}
		if err := store.FinishPeerControl(context.WithoutCancel(ctx), roomID, deliveryID, in.ID, status); err != nil {
			result = PeerControlResult{}
			resultErr = fmt.Errorf("操作结果未能持久保存，不会自动重试")
		}
	}()
	switch in.Action {
	case "stop":
		// Only this provider turn; do not cancel the session queue or another member.
		controller.sendMu.Lock()
		defer controller.sendMu.Unlock()
		controller.mu.Lock()
		current := controller.pendingTurnID
		controller.mu.Unlock()
		if current != run.ProviderTurnID || run.State != "running" {
			return PeerControlResult{}, ErrPeerRunChanged
		}
		if err := controller.conv.Interrupt(ctx, run.ProviderTurnID); err != nil {
			return PeerControlResult{}, err
		}
		return PeerControlResult{Status: "stop_requested"}, nil
	case "steer":
		if strings.TrimSpace(in.Text) == "" || len(in.Text) > 2000 || len(in.ID) == 0 || len(in.ID) > 100 {
			return PeerControlResult{}, fmt.Errorf("补充指令或操作编号无效")
		}
		controller.sendMu.Lock()
		defer controller.sendMu.Unlock()
		controller.mu.Lock()
		current := controller.pendingTurnID
		controller.mu.Unlock()
		if current != run.ProviderTurnID || run.State != "running" {
			return PeerControlResult{}, ErrPeerRunChanged
		}
		_, err = controller.steerExpectedLocked(peerpolicy.Admitted(ctx, "", run.SessionID), ports.ChatUserMessage{Text: in.Text, ClientMessageID: domain.PeerDeliveryKey(roomID, deliveryID+":"+in.ID)}, run.ProviderTurnID)
		if err != nil {
			return PeerControlResult{}, err
		}
		return PeerControlResult{Status: "steered"}, nil
	case "approve":
		controller.sendMu.Lock()
		defer controller.sendMu.Unlock()
		controller.mu.Lock()
		current := controller.pendingTurnID
		controller.mu.Unlock()
		if current != run.ProviderTurnID || run.State != "running" {
			return PeerControlResult{}, ErrPeerRunChanged
		}
		// Match the original request and the provider's actual offered choices.
		for _, a := range run.Activities {
			if a.Kind != domain.ActivityKind("approval") || a.Status != domain.ActivityStatus("pending") || a.RequestID == "" || a.RequestID != in.RequestID {
				continue
			}
			var detail struct {
				Decisions []struct {
					ID  string          `json:"id"`
					Raw json.RawMessage `json:"raw"`
				} `json:"decisions"`
			}
			if json.Unmarshal(a.Detail, &detail) != nil {
				break
			}
			for _, d := range detail.Decisions {
				if d.ID == in.Decision {
					if err := controller.Resolve(ctx, in.RequestID, ports.ChatDecision{ID: d.ID, Raw: d.Raw}); err != nil {
						return PeerControlResult{}, err
					}
					return PeerControlResult{Status: "decision_submitted"}, nil
				}
			}
		}
		return PeerControlResult{}, fmt.Errorf("审批请求已失效或客户端未提供此选项")
	default:
		return PeerControlResult{}, fmt.Errorf("不支持此运行操作")
	}
}
