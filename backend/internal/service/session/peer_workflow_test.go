package session

import (
	"context"
	"database/sql"
	"path/filepath"
	"strings"
	"sync"
	"testing"

	"github.com/aoagents/agent-orchestrator/backend/internal/domain"
	"github.com/aoagents/agent-orchestrator/backend/internal/peerpolicy"
	"github.com/aoagents/agent-orchestrator/backend/internal/ports"
	"github.com/aoagents/agent-orchestrator/backend/internal/storage/sqlite/sqlitetest"
)

type nativeFixture struct {
	*fakeCommander
	db      *sql.DB
	store   any
	mu      sync.Mutex
	pending []func() error
}

func (n *nativeFixture) SendWithOptions(ctx context.Context, id domain.SessionID, text string, _ *ports.SpawnAttachment, o ports.MessageDeliveryOptions) error {
	if err := peerpolicy.Check(ctx, n.store, o.SenderSessionID, string(id)); err != nil {
		return err
	}
	n.mu.Lock()
	defer n.mu.Unlock()
	n.pending = append(n.pending, func() error { return n.reply(ctx, id, text, o) })
	return nil
}
func (n *nativeFixture) reply(ctx context.Context, id domain.SessionID, text string, o ports.MessageDeliveryOptions) error {
	tx, err := n.db.BeginTx(ctx, nil)
	if err != nil {
		return err
	}
	defer tx.Rollback()
	key := o.ClientMessageID
	for _, q := range []struct {
		sql  string
		args []any
	}{
		{"INSERT OR IGNORE INTO conversations(id,scope,project_id,session_id,current_session_id,created_at,updated_at) VALUES(?,'session','p',?,?,CURRENT_TIMESTAMP,CURRENT_TIMESTAMP)", []any{string(id), string(id), string(id)}},
		{"INSERT INTO conversation_turns(id,conversation_id,handled_by_session_id,state,requested_at) VALUES(?,?,?,'completed',CURRENT_TIMESTAMP)", []any{key, string(id), string(id)}},
		{"INSERT INTO conversation_messages(id,conversation_id,turn_id,sequence,role,origin,text,client_message_id,created_at,updated_at) VALUES(?,?,?,(SELECT COALESCE(MAX(sequence),0)+1 FROM conversation_messages WHERE conversation_id=?),'user','human',?,?,CURRENT_TIMESTAMP,CURRENT_TIMESTAMP)", []any{key + ":input", string(id), key, string(id), text, key}},
		{"INSERT INTO conversation_messages(id,conversation_id,turn_id,sequence,role,origin,text,created_at,updated_at) VALUES(?,?,?,(SELECT COALESCE(MAX(sequence),0)+1 FROM conversation_messages WHERE conversation_id=?),'assistant','provider',?,CURRENT_TIMESTAMP,CURRENT_TIMESTAMP)", []any{key + ":reply", string(id), key, string(id), "answer from " + string(id)}},
	} {
		if _, err = tx.ExecContext(ctx, q.sql, q.args...); err != nil {
			return err
		}
	}
	return tx.Commit()
}
func TestPeerWorkflowBroadcastRepliesAndRevocation(t *testing.T) {
	ctx := context.Background()
	dir := t.TempDir()
	store := sqlitetest.MustOpenAt(t, dir)
	db, err := sql.Open("sqlite", filepath.Join(dir, "ao.db"))
	if err != nil {
		t.Fatal(err)
	}
	defer db.Close()
	db.SetMaxOpenConns(1)
	if _, err = db.Exec("PRAGMA busy_timeout=5000"); err != nil {
		t.Fatal(err)
	}
	if _, err = db.Exec("INSERT INTO projects(id,path,display_name,registered_at) VALUES('p','/tmp/p','p',CURRENT_TIMESTAMP)"); err != nil {
		t.Fatal(err)
	}
	for i, id := range []string{"a", "b", "stopped"} {
		if _, err = db.Exec("INSERT INTO sessions(id,project_id,num,kind,session_mode,activity_state,activity_last_at,created_at,updated_at) VALUES(?,'p',?,'worker','chat','idle',CURRENT_TIMESTAMP,CURRENT_TIMESTAMP,CURRENT_TIMESTAMP)", id, i+1); err != nil {
			t.Fatal(err)
		}
	}
	if _, err = db.Exec("UPDATE sessions SET activity_state='exited' WHERE id='stopped'"); err != nil {
		t.Fatal(err)
	}
	fixture := &nativeFixture{fakeCommander: &fakeCommander{}, db: db, store: store}
	svc := NewWithDeps(Deps{Store: store, Manager: fixture})
	room, err := svc.ConfigurePeerRoom(ctx, domain.PeerRoom{ID: "r", ProjectID: "p", Members: []string{"a", "b", "stopped"}, Budget: 4})
	if err != nil {
		t.Fatal(err)
	}
	input := PeerBroadcastRequest{ID: "broadcast", Epoch: room.Epoch, Message: "discuss and propose tasks", Targets: []string{"a", "b"}, ConnectPeers: true}
	result, err := svc.BroadcastPeerRoom(ctx, "r", input)
	if err != nil {
		t.Fatal(err)
	}
	for _, d := range result.Deliveries {
		if d.State != "accepted" {
			t.Fatal(result)
		}
	}
	for _, reply := range fixture.pending {
		if err := reply(); err != nil {
			t.Fatal(err)
		}
	}
	history, err := svc.ListPeerDeliveries(ctx, "r")
	if err != nil || len(history) != 2 {
		t.Fatal(history, err)
	}
	for _, d := range history {
		if len(d.Replies) != 1 || d.Replies[0].Text != "answer from "+d.Target {
			t.Fatal(d)
		}
	}
	duplicate, err := svc.BroadcastPeerRoom(ctx, "r", input)
	if err != nil {
		t.Fatal(err)
	}
	for _, d := range duplicate.Deliveries {
		if d.State != "already_accepted" {
			t.Fatal(duplicate)
		}
	}

	for _, member := range []string{"a", "b"} {
		if _, err := svc.GetPeerToolBinding(ctx, member); err != nil {
			t.Fatal(err)
		}
	}
	var sent string
	if err := db.QueryRow("SELECT text FROM conversation_messages WHERE role='user' LIMIT 1").Scan(&sent); err != nil || !strings.Contains(sent, `"recipients":["a","b"]`) {
		t.Fatal(sent, err)
	}
	before := len(fixture.pending)
	if err := svc.SendPeerMessage(ctx, "r", domain.PeerMessage{ID: "peer-new", Epoch: room.Epoch, Sender: "a", Target: "b", Message: "Please independently verify my proposal"}); err != nil {
		t.Fatal(err)
	}
	for _, reply := range fixture.pending[before:] {
		if err := reply(); err != nil {
			t.Fatal(err)
		}
	}
	history, err = svc.ListPeerDeliveries(ctx, "r")
	if err != nil || len(history) != 3 {
		t.Fatal(history, err)
	}
	found := false
	for _, d := range history {
		if d.ID == "peer-new" {
			found = d.Sender == "a" && d.Target == "b" && len(d.Replies) == 1 && d.TurnState == "completed"
		}
	}
	if !found {
		t.Fatal("peer reply was not correlated", history)
	}
	room.Paused = true
	room, err = svc.ConfigurePeerRoom(ctx, room)
	if err != nil {
		t.Fatal(err)
	}
	if _, err = svc.GetPeerToolBinding(ctx, "a"); err == nil {
		t.Fatal("paused grant still valid")
	}
	room.Paused = false
	room, err = svc.ConfigurePeerRoom(ctx, room)
	if err != nil {
		t.Fatal(err)
	}
	if _, err = svc.GetPeerToolBinding(ctx, "a"); err == nil {
		t.Fatal("resume silently upgraded grant")
	}
	if _, err = svc.BindPeerTools(ctx, "r", room.Epoch); err != nil {
		t.Fatal(err)
	}
	if binding, err := svc.GetPeerToolBinding(ctx, "a"); err != nil || binding.Epoch != room.Epoch {
		t.Fatal(binding, err)
	}
}

// Exercise real SQLite admission/history joins and native-turn persistence using
// a deterministic provider. No real accounts or existing conversations are used.
func TestPeerContextDurableDisableReenableWorkflow(t *testing.T) {
	ctx := context.Background()
	dir := t.TempDir()
	store := sqlitetest.MustOpenAt(t, dir)
	db, err := sql.Open("sqlite", filepath.Join(dir, "ao.db"))
	if err != nil {
		t.Fatal(err)
	}
	defer db.Close()
	db.SetMaxOpenConns(1)
	exec := func(q string, args ...any) {
		t.Helper()
		if _, err := db.Exec(q, args...); err != nil {
			t.Fatal(err)
		}
	}
	exec("PRAGMA busy_timeout=5000")
	exec("INSERT INTO projects(id,path,display_name,registered_at) VALUES('p','/tmp/p','p',CURRENT_TIMESTAMP)")
	for i, id := range []string{"a", "b"} {
		exec("INSERT INTO sessions(id,project_id,num,kind,session_mode,activity_state,activity_last_at,created_at,updated_at) VALUES(?,'p',?,'worker','chat','idle',CURRENT_TIMESTAMP,CURRENT_TIMESTAMP,CURRENT_TIMESTAMP)", id, i+1)
	}
	fixture := &nativeFixture{fakeCommander: &fakeCommander{}, db: db, store: store}
	svc := NewWithDeps(Deps{Store: store, Manager: fixture})
	room, err := svc.ConfigurePeerRoom(ctx, domain.PeerRoom{ID: "r", ProjectID: "p", Members: []string{"a", "b"}, Budget: 24})
	if err != nil {
		t.Fatal(err)
	}
	send := func(id, message string, targets []string) {
		t.Helper()
		before := len(fixture.pending)
		out, err := svc.BroadcastPeerRoom(ctx, "r", PeerBroadcastRequest{ID: id, Epoch: room.Epoch, Message: message, Targets: targets, ConnectPeers: true})
		if err != nil {
			t.Fatal(err)
		}
		for _, d := range out.Deliveries {
			if d.State != "accepted" {
				t.Fatal(out)
			}
		}
		for _, f := range fixture.pending[before:] {
			if err := f(); err != nil {
				t.Fatal(err)
			}
		}
	}
	send("first", "基线讨论", []string{"a", "b"})
	exec("UPDATE peer_deliveries SET created_at='2026-11-01T00:00:00Z'")
	exec("UPDATE conversation_messages SET created_at='2026-11-01T00:00:00Z'")
	room.MemberSettings = map[string]domain.PeerMemberSettings{"a": {Disabled: true}}
	room, err = svc.ConfigurePeerRoom(ctx, room)
	if err != nil {
		t.Fatal(err)
	}
	send("while-away", "决定：只做本地，不部署", []string{"b"})
	exec("UPDATE conversation_messages SET text='讨论结论：保留本地模式，任务交给 b' WHERE role='assistant' AND id LIKE ?", domain.PeerDeliveryKey("r", func() string {
		var id string
		if err := db.QueryRow("SELECT id FROM peer_deliveries WHERE broadcast_id='while-away'").Scan(&id); err != nil {
			t.Fatal(err)
		}
		return id
	}())+"%")
	if _, err = svc.CreatePeerTask(ctx, "r", domain.PeerTask{ID: "local-check", Goal: "验证本地模式"}); err != nil {
		t.Fatal(err)
	}
	before := len(fixture.pending)
	room.MemberSettings["a"] = domain.PeerMemberSettings{}
	room, err = svc.ConfigurePeerRoom(ctx, room)
	if err != nil {
		t.Fatal(err)
	}
	if len(fixture.pending) != before {
		t.Fatal("enabling triggered work")
	}
	// Reconstruct service: catch-up must rely on durable records, not process memory.
	svc = NewWithDeps(Deps{Store: store, Manager: fixture})
	send("return", "只解释现在结论和任务，不执行", []string{"a"})
	var prompt string
	if err = db.QueryRow("SELECT text FROM conversation_messages WHERE conversation_id='a' AND role='user' ORDER BY sequence DESC LIMIT 1").Scan(&prompt); err != nil {
		t.Fatal(err)
	}
	for _, want := range []string{"决定：只做本地，不部署", "讨论结论：保留本地模式", "local-check", "禁止据此重发消息、重复执行"} {
		if !strings.Contains(prompt, want) {
			t.Fatalf("missing %s in %s", want, prompt)
		}
	}
	if strings.Contains(prompt, "基线讨论") {
		t.Fatal("old input unnecessarily replayed as background")
	}
	var count int
	if err = db.QueryRow("SELECT COUNT(*) FROM peer_deliveries").Scan(&count); err != nil || count != 4 {
		t.Fatal(count, err)
	}
	out, err := svc.BroadcastPeerRoom(ctx, "r", PeerBroadcastRequest{ID: "return", Epoch: room.Epoch, Message: "只解释现在结论和任务，不执行", Targets: []string{"a"}})
	if err != nil || out.Deliveries[0].State != "already_accepted" || len(fixture.pending) != before+1 {
		t.Fatal("replayed", out, err)
	}
}
