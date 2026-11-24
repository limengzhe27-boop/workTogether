package session

import (
	"context"
	"errors"
	"fmt"
	"github.com/aoagents/agent-orchestrator/backend/internal/ports"
	"strings"
	"sync"
	"sync/atomic"
	"testing"
	"time"

	"github.com/aoagents/agent-orchestrator/backend/internal/domain"
)

type broadcastStore struct {
	peerStoreFake
	mu          sync.Mutex
	room        domain.PeerRoom
	messages    []domain.PeerMessage
	states      map[string]string
	used        int
	unavailable bool
}

func (p *broadcastStore) GetSession(_ context.Context, id domain.SessionID) (domain.SessionRecord, bool, error) {
	return domain.SessionRecord{ID: id, ProjectID: domain.ProjectID(p.room.ProjectID), Mode: domain.SessionModeChat, IsTerminated: p.unavailable}, true, nil
}
func (p *broadcastStore) GetPeerRoom(context.Context, string) (domain.PeerRoom, error) {
	return p.room, nil
}
func (p *broadcastStore) ReserveUserRoomMessage(_ context.Context, _ string, m domain.PeerMessage) (bool, error) {
	p.mu.Lock()
	defer p.mu.Unlock()
	if state := p.states[m.ID]; state != "" {
		if state == "accepted" {
			return false, nil
		}
		return false, errors.New("uncertain")
	}
	if p.used >= p.room.Budget {
		return false, errors.New("budget")
	}
	p.used++
	p.messages = append(p.messages, m)
	p.states[m.ID] = "pending"
	return true, nil
}
func (p *broadcastStore) FinishPeerMessage(_ context.Context, _, id string, ok bool) error {
	p.mu.Lock()
	defer p.mu.Unlock()
	p.states[id] = "uncertain"
	if ok {
		p.states[id] = "accepted"
	}
	return nil
}
func TestPeerBroadcastParallelAndNoReplay(t *testing.T) {
	p := &broadcastStore{room: domain.PeerRoom{Epoch: 1, Budget: 2, Members: []string{"a", "b"}}, states: map[string]string{}}
	arrived := make(chan string, 2)
	release := make(chan struct{})
	var calls atomic.Int32
	m := &fakeCommander{sendFunc: func(id domain.SessionID, _ string) error {
		calls.Add(1)
		arrived <- string(id)
		<-release
		if id == "b" {
			return errors.New("lost receipt")
		}
		return nil
	}}
	s := NewWithDeps(Deps{Store: p, Manager: m})
	in := PeerBroadcastRequest{ID: "broadcast", Epoch: 1, Message: "hello"}
	done := make(chan PeerBroadcastResult, 1)
	go func() {
		out, err := s.BroadcastPeerRoom(context.Background(), "r", in)
		if err != nil {
			t.Error(err)
		}
		done <- out
	}()
	for i := 0; i < 2; i++ {
		select {
		case <-arrived:
		case <-time.After(3 * time.Second):
			close(release)
			t.Fatal("fanout did not start both recipients")
		}
	}
	close(release)
	out := <-done
	if out.Deliveries[0].State != "accepted" || out.Deliveries[1].State != "uncertain" {
		t.Fatal(out)
	}
	for _, message := range p.messages {
		if message.BroadcastID != in.ID {
			t.Fatalf("missing broadcast grouping: %+v", message)
		}
	}
	again, err := s.BroadcastPeerRoom(context.Background(), "r", in)
	if err != nil || calls.Load() != 2 || again.Deliveries[0].State != "already_accepted" || again.Deliveries[1].State != "blocked" {
		t.Fatal(again, err, calls.Load())
	}
}
func TestPeerBroadcastReportsPartialBudget(t *testing.T) {
	p := &broadcastStore{room: domain.PeerRoom{Epoch: 1, Budget: 1, Members: []string{"a", "b"}}, states: map[string]string{}}
	var calls atomic.Int32
	s := NewWithDeps(Deps{Store: p, Manager: &fakeCommander{sendFunc: func(domain.SessionID, string) error { calls.Add(1); return nil }}})
	out, err := s.BroadcastPeerRoom(context.Background(), "r", PeerBroadcastRequest{ID: "x", Epoch: 1, Message: "hi"})
	if err != nil || calls.Load() != 1 || out.Deliveries[0].State != "accepted" || out.Deliveries[1].State != "blocked" {
		t.Fatal(out, err)
	}
}

func TestPeerBroadcastPreflightsTheWholeFanoutBeforeProvidersRun(t *testing.T) {
	p := &broadcastStore{room: domain.PeerRoom{Epoch: 1, Budget: 2, Members: []string{"a", "b", "blocked"}}, states: map[string]string{}}
	s := NewWithDeps(Deps{Store: p, Manager: &fakeCommander{sendFunc: func(_ domain.SessionID, prompt string) error {
		p.mu.Lock()
		defer p.mu.Unlock()
		if len(p.messages) != 2 {
			t.Error("provider started before the other recipient was reserved")
		}
		if !strings.Contains(prompt, `"recipients":["a","b"]`) || strings.Contains(prompt, `"blocked"`) {
			t.Errorf("provider got a recipient that did not pass preflight")
		}
		return nil
	}}})
	out, err := s.BroadcastPeerRoom(context.Background(), "r", PeerBroadcastRequest{ID: "preflight", Epoch: 1, Message: "hello"})
	if err != nil || len(out.Deliveries) != 3 || out.Deliveries[2].State != "blocked" {
		t.Fatal(out, err)
	}
}

func TestPeerBroadcastUnavailableDoesNotReserveOrSend(t *testing.T) {
	p := &broadcastStore{room: domain.PeerRoom{Epoch: 1, Budget: 2, Members: []string{"a"}}, states: map[string]string{}, unavailable: true}
	var calls int
	s := NewWithDeps(Deps{Store: p, Manager: &fakeCommander{sendFunc: func(domain.SessionID, string) error { calls++; return nil }}})
	out, err := s.BroadcastPeerRoom(context.Background(), "r", PeerBroadcastRequest{ID: "x", Epoch: 1, Message: "hello"})
	if err != nil || calls != 0 || p.used != 0 || out.Deliveries[0].State != "blocked" || out.Deliveries[0].Detail == "" {
		t.Fatal(out, err, calls, p.used)
	}
}

func TestPeerDeliveryFailureExplainsKnownCauseWithoutLeakingProviderText(t *testing.T) {
	got := peerDeliveryFailure(fmt.Errorf("secret-token: %w", ports.ErrChatAuthRequired))
	if !strings.Contains(got, "登录") || strings.Contains(got, "secret-token") {
		t.Fatal(got)
	}
	got = peerDeliveryFailure(errors.New("secret-token"))
	if !strings.Contains(got, "未分类") || strings.Contains(got, "secret-token") {
		t.Fatal(got)
	}
}

func TestPeerBroadcastExplicitRecipientsAndContext(t *testing.T) {
	p := &broadcastStore{room: domain.PeerRoom{Epoch: 1, Budget: 4, Members: []string{"a", "b", "stopped"}}, states: map[string]string{}}
	var mu sync.Mutex
	prompts := map[string]string{}
	svc := NewWithDeps(Deps{Store: p, Manager: &fakeCommander{sendFunc: func(id domain.SessionID, text string) error {
		mu.Lock()
		defer mu.Unlock()
		prompts[string(id)] = text
		return nil
	}}})
	for _, targets := range [][]string{{"outsider"}, {"a", "a"}, {}} {
		_, err := svc.BroadcastPeerRoom(context.Background(), "r", PeerBroadcastRequest{ID: "bad", Epoch: 1, Message: "hi", Targets: targets, ConnectPeers: true})
		if err == nil || p.used != 0 {
			t.Fatal("invalid target caused side effects", err, p.used)
		}
	}
	out, err := svc.BroadcastPeerRoom(context.Background(), "r", PeerBroadcastRequest{ID: "new", Epoch: 1, Message: "hello team", Targets: []string{"a", "b"}})
	if err != nil || len(out.Deliveries) != 2 || len(prompts) != 2 {
		t.Fatal(out, err, prompts)
	}
	for id, prompt := range prompts {
		if !strings.Contains(prompt, `"self":"`+id+`"`) || !strings.Contains(prompt, `"recipients":["a","b"]`) || !strings.Contains(prompt, "hello team") || strings.Contains(prompt, "stopped") {
			t.Fatal(prompt)
		}
	}
}

type attachmentCommander struct {
	*fakeCommander
	stage func(domain.SessionID, []ports.SpawnAttachment) ([]string, error)
}

func (m *attachmentCommander) StageAttachments(_ context.Context, id domain.SessionID, a []ports.SpawnAttachment) ([]string, error) {
	return m.stage(id, a)
}
func TestPeerBroadcastAttachmentsPerRecipientAndNoReplay(t *testing.T) {
	for _, fail := range []bool{false, true} {
		t.Run(fmt.Sprint(fail), func(t *testing.T) {
			p := &broadcastStore{room: domain.PeerRoom{Epoch: 1, Budget: 4, Members: []string{"a", "b"}}, states: map[string]string{}}
			var staged, sent atomic.Int32
			m := &attachmentCommander{fakeCommander: &fakeCommander{sendFunc: func(id domain.SessionID, prompt string) error {
				sent.Add(1)
				for _, fragment := range []string{".ao/attachments/" + string(id) + ".txt", ".ao/attachments/" + string(id) + ".png", `original name: "说明.txt"`, "SHA256:"} {
					if !strings.Contains(prompt, fragment) {
						t.Errorf("missing %s in %s", fragment, prompt)
					}
				}
				return nil
			}}, stage: func(id domain.SessionID, files []ports.SpawnAttachment) ([]string, error) {
				staged.Add(1)
				if len(files) != 2 || string(files[0].Data) != "proof" || files[1].Ext != ".png" {
					t.Errorf("wrong attachment bytes: %+v", files)
				}
				if fail && id == "b" {
					return nil, errors.New("private internal path")
				}
				return []string{".ao/attachments/" + string(id) + ".txt", ".ao/attachments/" + string(id) + ".png"}, nil
			}}
			svc := NewWithDeps(Deps{Store: p, Manager: m})
			in := PeerBroadcastRequest{ID: "files", Epoch: 1, Attachments: []PeerBroadcastAttachment{{Name: "说明.txt", MimeType: "text/plain"}, {Name: "figure.png", MimeType: "image/png"}}, DecodedAttachments: []ports.SpawnAttachment{{Ext: ".txt", Data: []byte("proof")}, {Ext: ".png", Data: []byte("png")}}}
			out, err := svc.BroadcastPeerRoom(context.Background(), "r", in)
			if err != nil || out.Deliveries[0].State != "accepted" {
				t.Fatal(out, err)
			}
			want := int32(2)
			if fail {
				want = 1
				if out.Deliveries[1].State != "uncertain" || !strings.Contains(out.Deliveries[1].Detail, "附件保存未完成") || strings.Contains(out.Deliveries[1].Detail, "private") {
					t.Fatal(out)
				}
			}
			if sent.Load() != want || staged.Load() != 2 {
				t.Fatal(sent.Load(), staged.Load())
			}
			for _, msg := range p.messages {
				if len(msg.Attachments) != 2 || msg.Attachments[0].Size != 5 || len(msg.Attachments[0].SHA256) != 64 {
					t.Fatal(msg)
				}
			}
			_, err = svc.BroadcastPeerRoom(context.Background(), "r", in)
			if err != nil || sent.Load() != want || staged.Load() != 2 {
				t.Fatal("replayed attachments", err, sent.Load(), staged.Load())
			}
			in.ID = "invalid"
			in.DecodedAttachments = nil
			if _, err = svc.BroadcastPeerRoom(context.Background(), "r", in); err == nil || staged.Load() != 2 {
				t.Fatal("unvalidated bytes admitted")
			}
		})
	}
}

func TestPeerDisplayNamesAndPrompt(t *testing.T) {
	rec := domain.SessionRecord{ID: "worktogether-3", Harness: "deepseek-harness"}
	if got := peerSessionName(rec); got != "DeepSeek 3" {
		t.Fatal(got)
	}
	rec.DisplayName = "小深"
	if got := peerSessionName(rec); got != "小深" {
		t.Fatal(got)
	}
	prompt := peerBroadcastPrompt("room", "worktogether-3", []string{"worktogether-3"}, PeerBroadcastRequest{Message: "hello"}, map[string]string{"worktogether-3": peerSessionName(rec)})
	if !strings.Contains(prompt, "小深") || !strings.Contains(prompt, "内部会话编号") || !strings.Contains(prompt, "[用户消息]\nhello") || !strings.Contains(prompt, "唯一的接收成员") {
		t.Fatal(prompt)
	}
}

func TestBroadcastResumeFailureDoesNotSendOrReplay(t *testing.T) {
	p := &broadcastStore{room: domain.PeerRoom{Epoch: 1, Budget: 24, Members: []string{"a"}}, states: map[string]string{}}
	calls := 0
	manager := &fakeCommander{restoreErr: errors.New("native resume failed"), sendFunc: func(domain.SessionID, string) error { calls++; return nil }}
	svc := NewWithDeps(Deps{Store: p, Manager: manager})
	request := PeerBroadcastRequest{ID: "resume-on-send", Epoch: 1, Message: "new message"}
	result, err := svc.BroadcastPeerRoom(context.Background(), "r", request)
	if err != nil || calls != 0 || len(manager.resumed) != 1 || result.Deliveries[0].State != "uncertain" {
		t.Fatal(result, err, calls, manager.resumed)
	}
	_, _ = svc.BroadcastPeerRoom(context.Background(), "r", request)
	if calls != 0 || len(manager.resumed) != 1 {
		t.Fatal("failed message replayed")
	}
}

func TestLongPeerBroadcastPreservesEachRecipientAndRejectsOversize(t *testing.T) {
	p := &broadcastStore{room: domain.PeerRoom{Epoch: 1, Budget: 2, Members: []string{"a", "b"}}, states: map[string]string{}}
	text := strings.Repeat("需求代码\n", 2000)
	var calls atomic.Int32
	m := &fakeCommander{sendFunc: func(id domain.SessionID, prompt string) error {
		if !strings.HasSuffix(prompt, text) {
			t.Errorf("truncated prompt for %s", id)
		}
		calls.Add(1)
		return nil
	}}
	s := NewWithDeps(Deps{Store: p, Manager: m})
	out, err := s.BroadcastPeerRoom(context.Background(), "r", PeerBroadcastRequest{ID: "long", Epoch: 1, Message: text})
	if err != nil || calls.Load() != 2 || len(out.Deliveries) != 2 {
		t.Fatal(out, err, calls.Load())
	}
	for _, msg := range p.messages {
		if msg.Message != text {
			t.Fatal("stored text changed")
		}
	}
	_, err = s.BroadcastPeerRoom(context.Background(), "r", PeerBroadcastRequest{ID: "oversize", Epoch: 1, Message: strings.Repeat("x", domain.PeerBroadcastMaxMessageBytes+1)})
	if err == nil || calls.Load() != 2 {
		t.Fatal("oversized message reached provider", err)
	}
}
