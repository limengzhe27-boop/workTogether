package controllers_test

import (
	"context"
	"database/sql"
	"errors"
	"io"
	"log/slog"
	"net/http"
	"net/http/httptest"
	"testing"

	"github.com/aoagents/agent-orchestrator/backend/internal/config"
	"github.com/aoagents/agent-orchestrator/backend/internal/domain"
	"github.com/aoagents/agent-orchestrator/backend/internal/httpd"
)

type unreadablePeerRoomService struct {
	*fakeSessionService
	err error
}

func (s *unreadablePeerRoomService) GetPeerRoom(context.Context, string) (domain.PeerRoom, error) {
	return domain.PeerRoom{}, s.err
}
func (s *unreadablePeerRoomService) ConfigurePeerRoom(context.Context, domain.PeerRoom) (domain.PeerRoom, error) {
	panic("GET must not configure a room")
}
func (s *unreadablePeerRoomService) SendPeerMessage(context.Context, string, domain.PeerMessage) error {
	panic("GET must not send a message")
}

func TestPeerRoomReadDistinguishesMissingAndUnavailable(t *testing.T) {
	for _, tc := range []struct {
		name string
		err  error
		want int
	}{
		{"missing", sql.ErrNoRows, http.StatusNotFound},
		{"unavailable", errors.New("storage unavailable"), http.StatusServiceUnavailable},
	} {
		t.Run(tc.name, func(t *testing.T) {
			svc := &unreadablePeerRoomService{fakeSessionService: newFakeSessionService(), err: tc.err}
			router := httpd.NewRouterWithControl(config.Config{}, slog.New(slog.NewTextHandler(io.Discard, nil)), nil, httpd.APIDeps{Sessions: svc}, httpd.ControlDeps{})
			srv := httptest.NewServer(router)
			defer srv.Close()
			_, status, _ := doRequest(t, srv, http.MethodGet, "/api/v1/peer-rooms/r", "")
			if status != tc.want {
				t.Fatalf("room returned %d, want %d", status, tc.want)
			}
		})
	}
}
