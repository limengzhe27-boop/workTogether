package sqlite

import (
	"context"
	"database/sql"
	"path/filepath"
	"strings"
	"testing"
	"time"

	"github.com/aoagents/agent-orchestrator/backend/internal/domain"
)

func TestPeerTaskReleasePreservesReviewsAndRejectsStaleOrDisabledActors(t *testing.T) {
	ctx := context.Background()
	dir := t.TempDir()
	s, err := Open(dir)
	if err != nil {
		t.Fatal(err)
	}
	defer s.Close()
	db, err := sql.Open("sqlite", filepath.Join(dir, "ao.db"))
	if err != nil {
		t.Fatal(err)
	}
	defer db.Close()

	now := time.Now().UTC()
	if _, err := db.Exec("INSERT INTO projects(id,path,display_name,registered_at) VALUES('p',?,'fixture',?)", dir, now); err != nil {
		t.Fatal(err)
	}
	for i, id := range []string{"owner", "reviewer", "peer"} {
		if _, err := db.Exec("INSERT INTO sessions(id,project_id,num,kind,activity_state,activity_last_at,created_at,updated_at) VALUES(?,'p',?,'worker','idle',?,?,?)", id, i+1, now, now, now); err != nil {
			t.Fatal(err)
		}
	}
	room, err := s.ConfigurePeerRoom(ctx, domain.PeerRoom{
		ID: "r", ProjectID: "p", Members: []string{"owner", "reviewer", "peer"}, Budget: 20,
	})
	if err != nil {
		t.Fatal(err)
	}
	task, err := s.CreatePeerTask(ctx, room.ID, domain.PeerTask{ID: "fix-login", Goal: "Fix and test login"})
	if err != nil {
		t.Fatal(err)
	}
	act := func(actor, action, evidence, commit string) {
		t.Helper()
		task, err = s.ActPeerTask(ctx, room.ID, domain.PeerTaskAction{
			ID: task.ID, Actor: actor, Action: action, Epoch: room.Epoch, Revision: task.Revision,
			Evidence: evidence, Commit: commit,
		})
		if err != nil {
			t.Fatal(err)
		}
	}
	first, second := strings.Repeat("a", 40), strings.Repeat("b", 40)
	act("owner", "claim", "", "")
	act("owner", "submit", strings.Repeat("x", 4000), first)
	act("reviewer", "reject", strings.Repeat("y", 4000), "")
	act("owner", "submit", "Retry after fixing the issue", second)
	if task.Reviewer != "" {
		t.Fatalf("new submission shows previous reviewer: %+v", task)
	}

	// Simulate persisted data from an older build that left the reviewer's name.
	if _, err := db.Exec("UPDATE peer_tasks SET reviewer='reviewer' WHERE room_id=? AND id=?", room.ID, task.ID); err != nil {
		t.Fatal(err)
	}
	list, err := s.ListPeerTasks(ctx, room.ID)
	if err != nil || len(list) != 1 || list[0].Reviewer != "" || list[0].ReviewCommit != "" {
		t.Fatalf("stale reviewer escaped the projection: %+v %v", list, err)
	}
	if _, err = s.ReleasePeerTask(ctx, room.ID, task.ID, room.Epoch, task.Revision); err == nil {
		t.Fatal("release replaced a submission awaiting review")
	}
	room.MemberSettings = map[string]domain.PeerMemberSettings{"reviewer": {Disabled: true}}
	room, err = s.ConfigurePeerRoom(ctx, room)
	if err != nil {
		t.Fatal(err)
	}
	if _, err = s.ActPeerTask(ctx, room.ID, domain.PeerTaskAction{
		ID: task.ID, Actor: "reviewer", Action: "accept", Epoch: room.Epoch, Revision: task.Revision, Evidence: "checked",
	}); err == nil {
		t.Fatal("disabled member could review the task")
	}
	act("peer", "reject", "Regression still fails", "")
	room.Members = []string{"reviewer", "peer"}
	room, err = s.ConfigurePeerRoom(ctx, room)
	if err != nil {
		t.Fatal(err)
	}
	if _, err := s.ReleasePeerTask(ctx, room.ID, task.ID, room.Epoch-1, task.Revision); err == nil {
		t.Fatal("stale room epoch released a claim")
	}
	if _, err := s.ReleasePeerTask(ctx, room.ID, task.ID, room.Epoch, task.Revision-1); err == nil {
		t.Fatal("stale task revision released a claim")
	}
	if _, err := s.ActPeerTask(ctx, room.ID, domain.PeerTaskAction{
		ID: task.ID, Action: "release", Epoch: room.Epoch, Revision: task.Revision,
	}); err == nil {
		t.Fatal("the member action endpoint released a claim")
	}

	released, err := s.ReleasePeerTask(ctx, room.ID, task.ID, room.Epoch, task.Revision)
	if err != nil || released.State != "open" || released.Owner != "" || released.Reviewer != "" || released.Commit != "" || released.Evidence != "" || released.Revision != task.Revision+1 {
		t.Fatalf("release did not return the task to the queue: %+v %v", released, err)
	}
	history, err := s.ListPeerTaskEvidence(ctx, room.ID, task.ID)
	if err != nil || len(history) != 6 || history[2].Action != "reject" || history[2].Commit != first || history[4].Action != "reject" || history[4].Commit != second || history[5].Action != "release" {
		t.Fatalf("release lost prior submissions or reviews: %+v %v", history, err)
	}
	task = released
	act("peer", "claim", "", "")
	if task.Owner != "peer" {
		t.Fatalf("another active member could not claim the released task: %+v", task)
	}
}
