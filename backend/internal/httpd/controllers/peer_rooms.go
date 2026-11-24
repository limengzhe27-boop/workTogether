package controllers

import (
	"context"
	"database/sql"
	"errors"
	"github.com/aoagents/agent-orchestrator/backend/internal/domain"
	"github.com/aoagents/agent-orchestrator/backend/internal/httpd/envelope"
	sessionsvc "github.com/aoagents/agent-orchestrator/backend/internal/service/session"
	"github.com/go-chi/chi/v5"
	"net/http"
)

type PeerRoomIDParam struct {
	RoomID string `path:"roomId"`
}
type PeerSendResponse struct {
	Accepted bool `json:"accepted"`
}
type peerRoomService interface {
	GetPeerRoom(context.Context, string) (domain.PeerRoom, error)
	ConfigurePeerRoom(context.Context, domain.PeerRoom) (domain.PeerRoom, error)
	SendPeerMessage(context.Context, string, domain.PeerMessage) error
}

func (c *SessionsController) peerRoom(w http.ResponseWriter, r *http.Request) {
	svc, ok := c.Svc.(peerRoomService)
	if !ok {
		http.Error(w, "unavailable", 503)
		return
	}
	id := chi.URLParam(r, "roomId")
	if r.Method == "GET" {
		room, err := svc.GetPeerRoom(r.Context(), id)
		if errors.Is(err, sql.ErrNoRows) {
			envelope.WriteAPIError(w, r, 404, "not_found", "PEER_ROOM_NOT_FOUND", "room unavailable", nil)
			return
		}
		if err != nil {
			envelope.WriteAPIError(w, r, 503, "unavailable", "PEER_ROOM_UNAVAILABLE", "Cannot load room settings", nil)
			return
		}
		envelope.WriteJSON(w, 200, room)
		return
	}
	r.Body = http.MaxBytesReader(w, r.Body, 8192)
	if r.Method == "PUT" {
		var room domain.PeerRoom
		if err := decodeJSON(r, &room); err != nil {
			envelope.WriteAPIError(w, r, 400, "bad_request", "INVALID_JSON", "invalid configuration", nil)
			return
		}
		room.ID = id
		result, err := svc.ConfigurePeerRoom(r.Context(), room)
		if err != nil {
			envelope.WriteAPIError(w, r, 409, "conflict", "PEER_ROOM_REJECTED", err.Error(), nil)
			return
		}
		envelope.WriteJSON(w, 200, result)
		return
	}
	var msg domain.PeerMessage
	if err := decodeJSON(r, &msg); err != nil {
		envelope.WriteAPIError(w, r, 400, "bad_request", "INVALID_JSON", "invalid message", nil)
		return
	}
	if err := svc.SendPeerMessage(r.Context(), id, msg); err != nil {
		envelope.WriteAPIError(w, r, 409, "conflict", "PEER_MESSAGE_REJECTED", err.Error(), nil)
		return
	}
	envelope.WriteJSON(w, 200, PeerSendResponse{true})
}

type peerTaskService interface {
	ListPeerTasks(context.Context, string) ([]domain.PeerTask, error)
	CreatePeerTask(context.Context, string, domain.PeerTask) (domain.PeerTask, error)
	ActPeerTask(context.Context, string, domain.PeerTaskAction) (domain.PeerTask, error)
}

func (c *SessionsController) peerTasks(w http.ResponseWriter, r *http.Request) {
	svc, ok := c.Svc.(peerTaskService)
	if !ok {
		http.Error(w, "unavailable", 503)
		return
	}
	id := chi.URLParam(r, "roomId")
	if r.Method == "GET" {
		tasks, err := svc.ListPeerTasks(r.Context(), id)
		if err != nil {
			envelope.WriteError(w, r, err)
			return
		}
		envelope.WriteJSON(w, 200, tasks)
		return
	}
	r.Body = http.MaxBytesReader(w, r.Body, 20000)
	var result domain.PeerTask
	var err error
	if r.Method == "PUT" {
		var t domain.PeerTask
		err = decodeJSON(r, &t)
		if err == nil {
			result, err = svc.CreatePeerTask(r.Context(), id, t)
		}
	} else {
		var a domain.PeerTaskAction
		err = decodeJSON(r, &a)
		if err == nil {
			result, err = svc.ActPeerTask(r.Context(), id, a)
		}
	}
	if err != nil {
		envelope.WriteAPIError(w, r, 409, "conflict", "PEER_TASK_REJECTED", err.Error(), nil)
		return
	}
	envelope.WriteJSON(w, 200, result)
}

type PeerTaskReleaseRequest struct {
	Epoch    int64 `json:"epoch"`
	Revision int64 `json:"revision"`
}

func (c *SessionsController) peerTaskRelease(w http.ResponseWriter, r *http.Request) {
	svc, ok := c.Svc.(interface {
		ReleasePeerTask(context.Context, string, string, int64, int64) (domain.PeerTask, error)
	})
	if !ok {
		http.Error(w, "unavailable", 503)
		return
	}
	r.Body = http.MaxBytesReader(w, r.Body, 1024)
	var in PeerTaskReleaseRequest
	if err := decodeJSON(r, &in); err != nil {
		envelope.WriteAPIError(w, r, 400, "bad_request", "INVALID_JSON", "invalid task release", nil)
		return
	}
	task, err := svc.ReleasePeerTask(r.Context(), chi.URLParam(r, "roomId"), chi.URLParam(r, "taskId"), in.Epoch, in.Revision)
	if err != nil {
		envelope.WriteAPIError(w, r, 409, "conflict", "PEER_TASK_RELEASE_REJECTED", err.Error(), nil)
		return
	}
	envelope.WriteJSON(w, 200, task)
}

func (c *SessionsController) peerIntegrate(w http.ResponseWriter, r *http.Request) {
	svc, ok := c.Svc.(interface {
		IntegratePeerRoom(context.Context, string, sessionsvc.PeerIntegrationRequest) (sessionsvc.PeerIntegrationResult, error)
	})
	if !ok {
		http.Error(w, "unavailable", 503)
		return
	}
	r.Body = http.MaxBytesReader(w, r.Body, 2048)
	var in sessionsvc.PeerIntegrationRequest
	if err := decodeJSON(r, &in); err != nil {
		envelope.WriteAPIError(w, r, 400, "bad_request", "INVALID_JSON", "invalid integration request", nil)
		return
	}
	out, err := svc.IntegratePeerRoom(r.Context(), chi.URLParam(r, "roomId"), in)
	if err != nil {
		envelope.WriteAPIError(w, r, 409, "conflict", "PEER_INTEGRATION_REJECTED", err.Error(), nil)
		return
	}
	envelope.WriteJSON(w, 200, out)
}

func (c *SessionsController) peerBroadcast(w http.ResponseWriter, r *http.Request) {
	svc, ok := c.Svc.(interface {
		BroadcastPeerRoom(context.Context, string, sessionsvc.PeerBroadcastRequest) (sessionsvc.PeerBroadcastResult, error)
	})
	if !ok {
		http.Error(w, "unavailable", 503)
		return
	}
	r.Body = http.MaxBytesReader(w, r.Body, maxSpawnBodyBytes)
	var in sessionsvc.PeerBroadcastRequest
	if err := decodeJSON(r, &in); err != nil {
		envelope.WriteAPIError(w, r, 400, "bad_request", "INVALID_JSON", "invalid broadcast", nil)
		return
	}
	raw := make([]AttachmentInput, len(in.Attachments))
	for i, a := range in.Attachments {
		raw[i] = AttachmentInput{MimeType: a.MimeType, Data: a.Data}
	}
	decoded, attachErr := decodeSpawnAttachments(raw)
	if attachErr != nil {
		envelope.WriteAPIError(w, r, 400, "bad_request", attachErr.code, attachErr.message, nil)
		return
	}
	in.DecodedAttachments = decoded
	out, err := svc.BroadcastPeerRoom(r.Context(), chi.URLParam(r, "roomId"), in)
	if err != nil {
		envelope.WriteAPIError(w, r, 409, "conflict", "PEER_BROADCAST_REJECTED", err.Error(), nil)
		return
	}
	envelope.WriteJSON(w, 200, out)
}

func (c *SessionsController) peerHistory(w http.ResponseWriter, r *http.Request) {
	svc, ok := c.Svc.(interface {
		ListPeerDeliveries(context.Context, string) ([]domain.PeerDelivery, error)
	})
	if !ok {
		http.Error(w, "unavailable", 503)
		return
	}
	out, err := svc.ListPeerDeliveries(r.Context(), chi.URLParam(r, "roomId"))
	if err != nil {
		envelope.WriteAPIError(w, r, 503, "unavailable", "PEER_HISTORY_UNAVAILABLE", "Cannot load room history", nil)
		return
	}
	envelope.WriteJSON(w, 200, out)
}

func (c *SessionsController) peerBind(w http.ResponseWriter, r *http.Request) {
	svc, ok := c.Svc.(interface {
		BindPeerTools(context.Context, string, int64) (domain.PeerToolBinding, error)
	})
	if !ok {
		http.Error(w, "unavailable", 503)
		return
	}
	r.Body = http.MaxBytesReader(w, r.Body, 1024)
	var in domain.PeerBindRequest
	if err := decodeJSON(r, &in); err != nil {
		envelope.WriteAPIError(w, r, 400, "bad_request", "INVALID_JSON", "invalid binding request", nil)
		return
	}
	out, err := svc.BindPeerTools(r.Context(), chi.URLParam(r, "roomId"), in.Epoch)
	if err != nil {
		envelope.WriteAPIError(w, r, 409, "conflict", "PEER_BINDING_REJECTED", err.Error(), nil)
		return
	}
	envelope.WriteJSON(w, 200, out)
}
func (c *SessionsController) peerBinding(w http.ResponseWriter, r *http.Request) {
	svc, ok := c.Svc.(interface {
		GetPeerToolBinding(context.Context, string) (domain.PeerToolBinding, error)
	})
	if !ok {
		http.Error(w, "unavailable", 503)
		return
	}
	out, err := svc.GetPeerToolBinding(r.Context(), chi.URLParam(r, "sessionId"))
	if err != nil {
		envelope.WriteAPIError(w, r, 409, "conflict", "PEER_BINDING_UNAVAILABLE", "Tool binding unavailable or revoked; ask the user to reconnect room tools", nil)
		return
	}
	envelope.WriteJSON(w, 200, out)
}

type PeerDeliveryIDParam struct {
	DeliveryID string `path:"deliveryId"`
}
type PeerExecutionResponse struct {
	domain.PeerExecution
	Activities []ConversationActivityResponse `json:"activities"`
}

func (c *SessionsController) peerExecution(w http.ResponseWriter, r *http.Request) {
	svc, ok := c.Svc.(interface {
		LoadPeerExecution(context.Context, string, string) (domain.PeerExecution, error)
	})
	if !ok {
		http.Error(w, "unavailable", 503)
		return
	}
	execution, err := svc.LoadPeerExecution(r.Context(), chi.URLParam(r, "roomId"), chi.URLParam(r, "deliveryId"))
	if err != nil {
		envelope.WriteAPIError(w, r, 404, "not_found", "PEER_EXECUTION_UNAVAILABLE", "尚无可关联的原生运行记录", nil)
		return
	}
	out := PeerExecutionResponse{PeerExecution: execution, Activities: []ConversationActivityResponse{}}
	for _, a := range execution.Activities {
		out.Activities = append(out.Activities, conversationActivityPayload(a))
	}
	envelope.WriteJSON(w, 200, out)
}

type PeerTaskIDParam struct {
	TaskID string `path:"taskId"`
}

func (c *SessionsController) peerTaskEvidence(w http.ResponseWriter, r *http.Request) {
	svc, ok := c.Svc.(interface {
		ListPeerTaskEvidence(context.Context, string, string) ([]domain.PeerTaskAction, error)
	})
	if !ok {
		http.Error(w, "unavailable", 503)
		return
	}
	out, err := svc.ListPeerTaskEvidence(r.Context(), chi.URLParam(r, "roomId"), chi.URLParam(r, "taskId"))
	if err != nil {
		envelope.WriteError(w, r, err)
		return
	}
	envelope.WriteJSON(w, 200, out)
}
func (c *SessionsController) peerIntegrationEvidence(w http.ResponseWriter, r *http.Request) {
	svc, ok := c.Svc.(interface {
		ListPeerCandidates(context.Context, string) ([]domain.PeerCandidate, error)
		RecordPeerIntegrationCheck(context.Context, string, domain.PeerIntegrationCheck) error
	})
	if !ok {
		http.Error(w, "unavailable", 503)
		return
	}
	room := chi.URLParam(r, "roomId")
	if r.Method == "GET" {
		out, err := svc.ListPeerCandidates(r.Context(), room)
		if err != nil {
			envelope.WriteError(w, r, err)
			return
		}
		envelope.WriteJSON(w, 200, out)
		return
	}
	r.Body = http.MaxBytesReader(w, r.Body, 12000)
	var in domain.PeerIntegrationCheck
	if err := decodeJSON(r, &in); err != nil {
		envelope.WriteAPIError(w, r, 400, "bad_request", "INVALID_JSON", "测试记录格式无效", nil)
		return
	}
	if err := svc.RecordPeerIntegrationCheck(r.Context(), room, in); err != nil {
		envelope.WriteAPIError(w, r, 409, "conflict", "PEER_CHECK_REJECTED", "记录未保存，请检查候选版本和必填字段；不要重复提交。", nil)
		return
	}
	envelope.WriteJSON(w, 200, PeerSendResponse{true})
}
