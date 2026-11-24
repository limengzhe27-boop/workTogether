package cli

import (
	"encoding/json"
	"github.com/aoagents/agent-orchestrator/backend/internal/domain"
	"net/http"
	"net/http/httptest"
	"testing"
)

func TestPeerSendUsesBoundRoom(t *testing.T) {
	t.Setenv("AO_SESSION_ID", "sender")
	t.Setenv("AO_PEER_PROJECT", "p")
	t.Setenv("AO_PEER_ROOM", "r")
	t.Setenv("AO_PEER_EPOCH", "7")
	cfg := setConfigEnv(t)
	calls := 0
	srv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		calls++
		if r.Method != "POST" || r.URL.Path != "/api/v1/peer-rooms/r/send" {
			t.Error(r.URL)
		}
		var m domain.PeerMessage
		json.NewDecoder(r.Body).Decode(&m)
		if m.Sender != "sender" || m.Epoch != 7 || m.ID != "delivery-1" {
			t.Error(m)
		}
		w.Write([]byte(`{}`))
	}))
	defer srv.Close()
	writeRunFileFor(t, cfg, srv)
	result := runMCP(t, mcpCall("peer_send", map[string]any{"id": "delivery-1", "session": "receiver", "message": "hello"}))[0]
	if result.Result.IsError || calls != 1 {
		t.Fatalf("%+v calls=%d", result, calls)
	}
}
func TestPeerSendFailsClosed(t *testing.T) {
	t.Setenv("AO_SESSION_ID", "sender")
	t.Setenv("AO_PEER_PROJECT", "p")
	t.Setenv("AO_PEER_ROOM", "")
	if !runMCP(t, mcpCall("peer_send", map[string]any{"id": "x", "session": "receiver", "message": "hello"}))[0].Result.IsError {
		t.Fatal("unbound room accepted")
	}
}

func TestPeerSendReadsExplicitServerGrant(t *testing.T) {
	t.Setenv("AO_SESSION_ID", "sender")
	t.Setenv("AO_PEER_PROJECT", "")
	t.Setenv("AO_PEER_ROOM", "")
	cfg := setConfigEnv(t)
	sent := 0
	srv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		if r.URL.Path == "/api/v1/sessions/sender/peer-binding" {
			w.Write([]byte(`{"roomId":"room","projectId":"p","epoch":4}`))
			return
		}
		if r.URL.Path != "/api/v1/peer-rooms/room/send" {
			t.Error(r.URL)
			http.Error(w, "bad", 400)
			return
		}
		var m domain.PeerMessage
		json.NewDecoder(r.Body).Decode(&m)
		if m.Epoch != 4 || m.Sender != "sender" {
			t.Error(m)
		}
		sent++
		w.Write([]byte(`{}`))
	}))
	defer srv.Close()
	writeRunFileFor(t, cfg, srv)
	result := runMCP(t, mcpCall("peer_send", map[string]any{"id": "d", "session": "other", "message": "hello"}))[0]
	if result.Result.IsError || sent != 1 {
		t.Fatal(result, sent)
	}
}
