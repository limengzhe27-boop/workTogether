package controllers

import (
	"context"
	"errors"
	"github.com/aoagents/agent-orchestrator/backend/internal/httpd/envelope"
	chatsvc "github.com/aoagents/agent-orchestrator/backend/internal/service/chat"
	"github.com/go-chi/chi/v5"
	"net/http"
)

func (c *ConversationsController) peerControl(w http.ResponseWriter, r *http.Request) {
	svc, ok := c.Svc.(interface {
		ControlPeerExecution(context.Context, string, string, chatsvc.PeerControlRequest) (chatsvc.PeerControlResult, error)
	})
	if !ok {
		http.Error(w, "unavailable", 503)
		return
	}
	r.Body = http.MaxBytesReader(w, r.Body, 8192)
	var in chatsvc.PeerControlRequest
	if err := decodeJSON(r, &in); err != nil {
		envelope.WriteAPIError(w, r, 400, "bad_request", "INVALID_JSON", "操作格式无效", nil)
		return
	}
	out, err := svc.ControlPeerExecution(r.Context(), chi.URLParam(r, "roomId"), chi.URLParam(r, "deliveryId"), in)
	if err != nil {
		message := "操作未确认完成，请刷新运行状态后检查原生会话；不会自动重试。"
		switch {
		case errors.Is(err, chatsvc.ErrPeerRunChanged), errors.Is(err, chatsvc.ErrNoActiveTurn):
			message = "指定运行已结束或发生变化，未操作其他任务。请刷新执行记录。"
		case errors.Is(err, chatsvc.ErrSteerUnsupported):
			message = "此客户端不支持运行中追加指令；没有排队或重发。可打开原生会话查看支持的操作。"
		case errors.Is(err, chatsvc.ErrTurnNotSteerable):
			message = "此轮运行暂不接受追加指令；没有排队或重发。"
		}
		envelope.WriteAPIError(w, r, 409, "conflict", "PEER_CONTROL_REJECTED", message, nil)
		return
	}
	envelope.WriteJSON(w, 200, out)
}
