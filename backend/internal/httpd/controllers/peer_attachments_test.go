package controllers_test

import (
	"context"
	"github.com/aoagents/agent-orchestrator/backend/internal/config"
	"github.com/aoagents/agent-orchestrator/backend/internal/httpd"
	sessionsvc "github.com/aoagents/agent-orchestrator/backend/internal/service/session"
	"io"
	"log/slog"
	"net/http"
	"net/http/httptest"
	"testing"
)

type broadcastAttachmentService struct {
	*fakeSessionService
	calls int
	input sessionsvc.PeerBroadcastRequest
}

func (s *broadcastAttachmentService) BroadcastPeerRoom(_ context.Context, _ string, in sessionsvc.PeerBroadcastRequest) (sessionsvc.PeerBroadcastResult, error) {
	s.calls++
	s.input = in
	return sessionsvc.PeerBroadcastResult{}, nil
}
func TestPeerAttachmentsDecodeBeforeAdmission(t *testing.T) {
	svc := &broadcastAttachmentService{fakeSessionService: newFakeSessionService()}
	srv := httptest.NewServer(httpd.NewRouterWithControl(config.Config{}, slog.New(slog.NewTextHandler(io.Discard, nil)), nil, httpd.APIDeps{Sessions: svc}, httpd.ControlDeps{}))
	defer srv.Close()
	for _, body := range []string{`{"attachments":[{"mimeType":"image/svg+xml","data":"PHN2Zy8+"}]}`, `{"attachments":[{"mimeType":"image/png","data":"!!!"}]}`, pngBody(9)} {
		_, status, _ := doRequest(t, srv, http.MethodPost, "/api/v1/peer-rooms/r/broadcast", body)
		if status != 400 || svc.calls != 0 {
			t.Fatal(status, svc.calls)
		}
	}
	_, status, _ := doRequest(t, srv, http.MethodPost, "/api/v1/peer-rooms/r/broadcast", `{"id":"new","epoch":1,"attachments":[{"name":"图.png","mimeType":"image/png","data":"cG5n"}]}`)
	if status != 200 || svc.calls != 1 || len(svc.input.DecodedAttachments) != 1 || string(svc.input.DecodedAttachments[0].Data) != "png" || svc.input.Attachments[0].Name != "图.png" {
		t.Fatal(status, svc.input)
	}
}
