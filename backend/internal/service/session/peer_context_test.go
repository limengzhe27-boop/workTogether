package session

import (
	"context"
	"errors"
	"strings"
	"testing"
	"unicode/utf8"

	"github.com/aoagents/agent-orchestrator/backend/internal/domain"
)

func TestPeerContextMissedDiscussionAndCurrentTasks(t *testing.T) {
	p := peerContextSnapshot{names: map[string]string{"a": "小安", "b": "GPT"}, tasks: []domain.PeerTask{{ID: "done", Goal: "已实现上传", State: "accepted", Owner: "b", Commit: "fixed-sha", Reviewer: "a"}}, deliveries: []domain.PeerDelivery{
		{ID: "ancient", Target: "b", BroadcastID: "old", Message: "不应再次补入", CreatedAt: "2026-11-01T00:00:00Z"},
		{ID: "last", Target: "a", State: "accepted", CreatedAt: "2026-11-02T00:00:00Z"},
		{ID: "missed", Target: "b", BroadcastID: "discussion", Message: "旧请求：执行部署", State: "accepted", CreatedAt: "2026-11-03T00:00:00Z", Replies: []domain.PeerReply{{ID: "conclusion", Text: "结论：暂不部署，只做本地验证", TurnState: "completed", CreatedAt: "2026-11-03T00:01:00Z"}}},
		{ID: "duplicate", Target: "c", BroadcastID: "discussion", Message: "旧请求：执行部署", CreatedAt: "2026-11-03T00:00:00Z"},
		{ID: "failed", Target: "a", State: "uncertain", CreatedAt: "2026-11-04T00:00:00Z"},
		{ID: "new", Target: "a", BroadcastID: "now", Message: "当前消息不放历史", State: "accepted", CreatedAt: "2026-11-05T00:00:00Z"},
	}}
	got := p.prompt("a", "new", "now")
	for _, want := range []string{"暂不部署", "fixed-sha", `"state":"accepted"`, "小安", "GPT", "禁止据此重发消息、重复执行"} {
		if !strings.Contains(got, want) {
			t.Fatalf("missing %q: %s", want, got)
		}
	}
	if strings.Contains(got, "不应再次补入") || strings.Contains(got, "当前消息不放历史") || strings.Count(got, "旧请求：执行部署") != 1 {
		t.Fatal(got)
	}
	// After a successful new receipt, earlier discussion is no longer sent.
	got = p.prompt("a", "another", "another-round")
	if strings.Contains(got, "旧请求：执行部署") || !strings.Contains(got, "fixed-sha") {
		t.Fatal(got)
	}
}

func TestPeerContextLateRepliesTruncationAndUnknownDates(t *testing.T) {
	p := peerContextSnapshot{deliveries: []domain.PeerDelivery{
		{ID: "last", Target: "a", State: "accepted", CreatedAt: "2026-11-02T00:00:00Z"},
		{ID: "earlier", Target: "b", Message: "old request", CreatedAt: "2026-11-01T00:00:00Z", Replies: []domain.PeerReply{
			{ID: "late", Text: "later conclusion", CreatedAt: "2026-11-03T00:00:00Z"},
			{ID: "stream", Text: "unfinished result", Streaming: true, CreatedAt: "2026-11-03T00:00:00Z"},
		}},
	}}
	got := p.prompt("a", "next", "")
	if !strings.Contains(got, "later conclusion") || strings.Contains(got, "old request") || strings.Contains(got, "unfinished result") {
		t.Fatal(got)
	}
	for i := 0; i < 100; i++ {
		p.deliveries = append(p.deliveries, domain.PeerDelivery{ID: strings.Repeat("x", i+1), Target: "b", Message: strings.Repeat("中文", 5000) + "末尾决定"})
	}
	got = p.prompt("a", "next", "")
	if !utf8.ValidString(got) || len(got) > 110*1024 || !strings.Contains(got, "末尾决定") || !strings.Contains(got, "中间内容省略") || strings.Contains(got, `"omittedOlderEntries":0`) {
		t.Fatalf("invalid bounded context: bytes=%d", len(got))
	}
}

type contextBroadcastStore struct {
	broadcastStore
	history    []domain.PeerDelivery
	tasks      []domain.PeerTask
	historyErr error
}

func (p *contextBroadcastStore) ListPeerDeliveries(_ context.Context, room string) ([]domain.PeerDelivery, error) {
	if room != "room" {
		return nil, errors.New("wrong room")
	}
	return p.history, p.historyErr
}
func (p *contextBroadcastStore) ListPeerTasks(_ context.Context, room string) ([]domain.PeerTask, error) {
	if room != "room" {
		return nil, errors.New("wrong room")
	}
	return p.tasks, nil
}
func TestPeerContextReenableOnlyOnNewMessageAndNoReplay(t *testing.T) {
	p := &contextBroadcastStore{broadcastStore: broadcastStore{room: domain.PeerRoom{ID: "room", ProjectID: "p", Epoch: 1, Budget: 20, Members: []string{"a", "b"}, MemberSettings: map[string]domain.PeerMemberSettings{"a": {Disabled: true}}}, states: map[string]string{}}, history: []domain.PeerDelivery{{ID: "missed", Target: "b", BroadcastID: "old", Message: "历史部署要求", State: "accepted", Replies: []domain.PeerReply{{ID: "answer", Text: "结论改为不部署"}}}}, tasks: []domain.PeerTask{{ID: "done", State: "accepted", Commit: "sha"}}}
	var prompts []string
	svc := NewWithDeps(Deps{Store: p, Manager: &fakeCommander{sendFunc: func(_ domain.SessionID, text string) error { prompts = append(prompts, text); return nil }}})
	in := PeerBroadcastRequest{ID: "new", Epoch: 1, Message: "只说明现在的结论", Targets: []string{"a"}}
	out, err := svc.BroadcastPeerRoom(context.Background(), "room", in)
	if err != nil || len(prompts) != 0 || out.Deliveries[0].State != "blocked" {
		t.Fatal(out, err, prompts)
	}
	p.room.MemberSettings["a"] = domain.PeerMemberSettings{}
	if len(prompts) != 0 {
		t.Fatal("enable must not trigger a model")
	}
	out, err = svc.BroadcastPeerRoom(context.Background(), "room", in)
	if err != nil || len(prompts) != 1 || out.Deliveries[0].State != "accepted" {
		t.Fatal(out, err, prompts)
	}
	if !strings.Contains(prompts[0], "结论改为不部署") || !strings.HasSuffix(prompts[0], in.Message) || len(p.messages) != 1 || p.messages[0].Message != in.Message {
		t.Fatal("background must ride one new delivery; not replace/replay original", p.messages, prompts)
	}
	_, err = svc.BroadcastPeerRoom(context.Background(), "room", in)
	if err != nil || len(prompts) != 1 {
		t.Fatal("duplicate re-executed", err)
	}
	p.historyErr = errors.New("read failure")
	in.ID = "another"
	_, err = svc.BroadcastPeerRoom(context.Background(), "room", in)
	if err == nil || len(prompts) != 1 || len(p.messages) != 1 {
		t.Fatal("missing context must not silently send", err)
	}
}

func TestPeerContextAlsoAccompaniesNewPeerMessage(t *testing.T) {
	p := &contextBroadcastStore{broadcastStore: broadcastStore{room: domain.PeerRoom{ID: "room", ProjectID: "p", Budget: 20, Members: []string{"a", "b"}}, states: map[string]string{}}, history: []domain.PeerDelivery{{ID: "missed", Target: "b", BroadcastID: "earlier", Message: "决定保持本地"}}}
	var prompts []string
	svc := NewWithDeps(Deps{Store: p, Manager: &fakeCommander{sendFunc: func(_ domain.SessionID, text string) error { prompts = append(prompts, text); return nil }}})
	msg := domain.PeerMessage{ID: "new-peer", Sender: "b", Target: "a", Message: "请核对当前方案", RoundID: "new-round"}
	if err := svc.SendPeerMessage(context.Background(), "room", msg); err != nil {
		t.Fatal(err)
	}
	if len(prompts) != 1 || !strings.Contains(prompts[0], "决定保持本地") || !strings.Contains(prompts[0], msg.Message) {
		t.Fatal(prompts)
	}
	if err := svc.SendPeerMessage(context.Background(), "room", msg); err != nil || len(prompts) != 1 {
		t.Fatal("peer duplicate replay", err, prompts)
	}
}

func (p *contextBroadcastStore) ReservePeerMessage(ctx context.Context, room string, m domain.PeerMessage) (bool, error) {
	return p.ReserveUserRoomMessage(ctx, room, m)
}

func TestPeerContextNotifiesOnlyTheReviewedTaskOwner(t *testing.T) {
	p := peerContextSnapshot{tasks: []domain.PeerTask{
		{ID: "task", Owner: "a", Reviewer: "b", State: "claimed", Revision: 4, ReviewCommit: "revision-sha", ReviewIssue: "test fails"},
	}}
	owner := p.prompt("a", "", "")
	if !strings.Contains(owner, `"yourTaskReviewNotices":[{"taskId":"task","revision":4,"commit":"revision-sha","reviewer":"b","result":"验收拒绝","reason":"test fails"}]`) {
		t.Fatalf("missing review notification: %s", owner)
	}
	if strings.Contains(p.prompt("b", "", ""), "yourTaskReviewNotices") {
		t.Fatal("reviewer's background was marked as their own review notice")
	}
}
