package sqlite

import (
	"context"
	"database/sql"
	"fmt"
	"github.com/aoagents/agent-orchestrator/backend/internal/domain"
	"path/filepath"
	"strings"
	"sync"
	"sync/atomic"
	"testing"
	"time"
)

func TestPeerRoomDurableAdmission(t *testing.T) {
	ctx := context.Background()
	dir := t.TempDir()
	s, err := Open(dir)
	if err != nil {
		t.Fatal(err)
	}
	db, err := sql.Open("sqlite", filepath.Join(dir, "ao.db"))
	if err != nil {
		t.Fatal(err)
	}
	defer db.Close()
	now := time.Now().UTC()
	for _, p := range []string{"p", "q"} {
		if _, err = db.Exec("INSERT INTO projects(id,path,display_name,registered_at) VALUES(?,?,?,?)", p, "/tmp/"+p, p, now); err != nil {
			t.Fatal(err)
		}
	}
	for i, id := range []string{"a", "b", "outsider", "cross"} {
		p := "p"
		if id == "cross" {
			p = "q"
		}
		_, err = db.Exec("INSERT INTO sessions(id,project_id,num,kind,activity_state,activity_last_at,created_at,updated_at) VALUES(?,?,?,'worker','idle',?,?,?)", id, p, i+1, now, now, now)
		if err != nil {
			t.Fatal(err)
		}
	}
	solo, err := s.ConfigurePeerRoom(ctx, domain.PeerRoom{ID: "solo", ProjectID: "p", Members: []string{"a"}, Budget: 2})
	if err != nil {
		t.Fatal(err)
	}
	if ok, err := s.ReserveUserRoomMessage(ctx, solo.ID, domain.PeerMessage{ID: "solo-input", Target: "a", Epoch: solo.Epoch, Message: "hello"}); err != nil || !ok {
		t.Fatal(ok, err)
	}
	room, err := s.ConfigurePeerRoom(ctx, domain.PeerRoom{ID: "r", ProjectID: "p", Members: []string{"a", "b"}, Budget: 2})
	if err != nil {
		t.Fatal(err)
	}

	candidate := domain.PeerCandidate{Commit: "1111111111111111111111111111111111111111", Branch: "codex/integration-fixture", Tasks: []string{"task"}}
	if err = s.RecordPeerCandidate(ctx, "r", candidate); err != nil {
		t.Fatal(err)
	}
	check := domain.PeerIntegrationCheck{ID: "test-1", Epoch: room.Epoch, Commit: candidate.Commit, Executor: "tester", Command: "pytest", Cwd: "/fixture", ExitCode: 1, Scope: "independent rerun failed; browser unverified"}
	if err = s.RecordPeerIntegrationCheck(ctx, "r", check); err != nil {
		t.Fatal(err)
	}
	if err = s.RecordPeerIntegrationCheck(ctx, "r", check); err == nil {
		t.Fatal("duplicate test evidence accepted")
	}
	check.ID = "test-2"
	check.Commit = "2222222222222222222222222222222222222222"
	if err = s.RecordPeerIntegrationCheck(ctx, "r", check); err == nil {
		t.Fatal("test attached to unknown candidate")
	}
	checks, err := s.ListPeerCandidates(ctx, "r")
	if err != nil || len(checks) != 1 || len(checks[0].Checks) != 1 || checks[0].Checks[0].ExitCode != 1 {
		t.Fatal(checks, err)
	}

	if _, err = s.GetPeerToolBinding(ctx, "a"); err == nil {
		t.Fatal("ungranted tools allowed")
	}
	if _, err = s.BindPeerTools(ctx, "r", room.Epoch+1); err == nil {
		t.Fatal("stale bind allowed")
	}
	if _, err = s.BindPeerTools(ctx, "r", room.Epoch); err != nil {
		t.Fatal(err)
	}
	if binding, err := s.GetPeerToolBinding(ctx, "a"); err != nil || binding.Epoch != room.Epoch {
		t.Fatal(binding, err)
	}
	proposed, err := s.ActPeerTask(ctx, "r", domain.PeerTaskAction{ID: "proposal", Actor: "a", Epoch: room.Epoch, Action: "propose", Goal: "independent task"})
	if err != nil || proposed.State != "open" {
		t.Fatal(proposed, err)
	}
	if _, err = s.ActPeerTask(ctx, "r", domain.PeerTaskAction{ID: "bad-proposal", Actor: "outsider", Epoch: room.Epoch, Action: "propose", Goal: "no"}); err == nil {
		t.Fatal("nonmember proposed")
	}
	msg := domain.PeerMessage{Sender: "a", Target: "b", ID: "one", BroadcastID: "broadcast-one", Epoch: room.Epoch, Message: "hello"}
	for _, target := range []string{"outsider", "cross", "a"} {
		bad := msg
		bad.Target = target
		if _, err = s.ReservePeerMessage(ctx, "r", bad); err == nil {
			t.Fatalf("accepted %s", target)
		}
	}
	var winners atomic.Int32
	var wg sync.WaitGroup
	for i := 0; i < 12; i++ {
		wg.Add(1)
		go func() {
			defer wg.Done()
			ok, _ := s.ReservePeerMessage(ctx, "r", msg)
			if ok {
				winners.Add(1)
			}
		}()
	}
	wg.Wait()
	if winners.Load() != 1 {
		t.Fatal(winners.Load())
	}
	s.Close()
	s, err = Open(dir)
	if err != nil {
		t.Fatal(err)
	}
	defer s.Close()
	history, err := s.ListPeerDeliveries(ctx, "r")
	if err != nil || len(history) != 1 || history[0].BroadcastID != "broadcast-one" || history[0].Message != "hello" || history[0].Sender != "a" || history[0].Target != "b" || history[0].State != "pending" || history[0].CreatedAt == "" {
		t.Fatalf("lost history after restart: %+v %v", history, err)
	}

	// Same-text unrelated turns must not appear; the persisted client key is the join.
	for _, q := range []string{
		"INSERT INTO conversations(id,scope,project_id,session_id,current_session_id,created_at,updated_at) VALUES('conv','session','p','b','b',CURRENT_TIMESTAMP,CURRENT_TIMESTAMP)",
		"INSERT INTO conversation_turns(id,conversation_id,handled_by_session_id,state,requested_at) VALUES('turn','conv','b','completed',CURRENT_TIMESTAMP),('unrelated','conv','b','completed',CURRENT_TIMESTAMP)",
		"INSERT INTO conversation_messages(id,conversation_id,turn_id,sequence,role,origin,text,created_at,updated_at) VALUES('reply','conv','turn',2,'assistant','provider','room answer','2026-11-13T09:00:01.123Z',CURRENT_TIMESTAMP),('other-reply','conv','unrelated',4,'assistant','provider','unrelated answer',CURRENT_TIMESTAMP,CURRENT_TIMESTAMP)",
	} {
		if _, err = db.Exec(q); err != nil {
			t.Fatal(err)
		}
	}
	if _, err = db.Exec("INSERT INTO conversation_messages(id,conversation_id,turn_id,sequence,role,origin,text,client_message_id,created_at,updated_at) VALUES('input','conv','turn',1,'user','human','hello',?,CURRENT_TIMESTAMP,CURRENT_TIMESTAMP)", domain.PeerDeliveryKey("r", "one")); err != nil {
		t.Fatal(err)
	}
	joined, err := s.ListPeerDeliveries(ctx, "r")
	if err != nil || len(joined[0].Replies) != 1 || joined[0].TurnState != "completed" || joined[0].Replies[0].Text != "room answer" || joined[0].Replies[0].CreatedAt != "2026-11-13T09:00:01.123Z" {
		t.Fatal(joined, err)
	}

	if _, err := db.Exec(`WITH RECURSIVE n(i) AS (VALUES (1) UNION ALL SELECT i+1 FROM n WHERE i<103)
		INSERT INTO conversation_messages(id,conversation_id,turn_id,sequence,role,origin,text,created_at,updated_at)
		SELECT 'long-reply-'||i,'conv','turn',1000+i,'assistant','provider','update',CURRENT_TIMESTAMP,CURRENT_TIMESTAMP FROM n`); err != nil {
		t.Fatal(err)
	}
	long, err := s.ListPeerDeliveries(ctx, "r")
	if err != nil || !long[0].RepliesTruncated || len(long[0].Replies) != 100 ||
		long[0].Replies[0].ID != "long-reply-4" || long[0].Replies[99].ID != "long-reply-103" {
		t.Fatal("latest updates were not retained in order", err)
	}
	if _, err := db.Exec("DELETE FROM conversation_messages WHERE id LIKE 'long-reply-%'"); err != nil {
		t.Fatal(err)
	}

	// Generated images follow the same exact turn, not the nearest assistant text.
	for _, item := range []struct {
		id, turn, actor string
		sequence        int
	}{
		{"image", "turn", "b", 30}, {"wrong-turn", "unrelated", "b", 31}, {"wrong-session", "turn", "a", 32},
	} {
		if _, err = db.Exec(`INSERT INTO conversation_activities(id,conversation_id,turn_id,sequence,kind,status,summary,detail_json,created_at,updated_at)
		  VALUES(?,'conv',?,?,'system','completed','Generated image',json_object('event','image.generated','image',json_object('name','PNG','url',?)),CURRENT_TIMESTAMP,CURRENT_TIMESTAMP)`,
			item.id, item.turn, item.sequence, "/api/v1/sessions/"+item.actor+"/artifact-files/generated-image-"+strings.Repeat("a", 64)+".png"); err != nil {
			t.Fatal(err)
		}
	}
	withImage, err := s.ListPeerDeliveries(ctx, "r")
	if err != nil || len(withImage[0].GeneratedImages) != 1 || withImage[0].GeneratedImages[0].ID != "image" {
		t.Fatalf("image was lost or associated with the wrong turn: %+v %v", withImage, err)
	}
	// A pending preview belongs to this arrival even before assistant text or a file exists.
	if _, err = db.Exec(`UPDATE conversation_turns SET state='running' WHERE id='turn';
		UPDATE conversation_activities SET status='running',detail_json=json_object('event','image.generated') WHERE id='image'`); err != nil {
		t.Fatal(err)
	}
	pendingImage, err := s.ListPeerDeliveries(ctx, "r")
	if err != nil || len(pendingImage[0].GeneratedImages) != 1 ||
		pendingImage[0].GeneratedImages[0].Status != domain.ActivityStatusRunning || pendingImage[0].GeneratedImages[0].URL != "" {
		t.Fatalf("missing a native pending image: %+v %v", pendingImage, err)
	}
	if _, err = db.Exec("UPDATE conversation_turns SET state='interrupted' WHERE id='turn'"); err != nil {
		t.Fatal(err)
	}
	stoppedImage, err := s.ListPeerDeliveries(ctx, "r")
	if err != nil || len(stoppedImage[0].GeneratedImages) != 1 || stoppedImage[0].GeneratedImages[0].Status != domain.ActivityStatusCancelled {
		t.Fatalf("stopped turn left an active image: %+v %v", stoppedImage, err)
	}
	if _, err = db.Exec("UPDATE conversation_turns SET state='completed' WHERE id='turn'"); err != nil {
		t.Fatal(err)
	}
	if _, err = db.Exec("DELETE FROM conversation_activities WHERE sequence BETWEEN 30 AND 32"); err != nil {
		t.Fatal(err)
	}

	// Submission anchors to the persisted active turn, ignoring any caller-supplied anchor.
	cardTask, err := s.CreatePeerTask(ctx, "r", domain.PeerTask{ID: "delivery-card", Goal: "fix login"})
	if err != nil {
		t.Fatal(err)
	}
	act := domain.PeerTaskAction{ID: cardTask.ID, Actor: "b", Epoch: room.Epoch, Action: "claim"}
	cardTask, err = s.ActPeerTask(ctx, "r", act)
	if err != nil {
		t.Fatal(err)
	}
	if _, err = db.Exec("UPDATE conversation_turns SET state='running' WHERE id='turn'"); err != nil {
		t.Fatal(err)
	}
	act.Action, act.Revision, act.DeliveryID = "submit", cardTask.Revision, "unrelated"
	act.Commit, act.Evidence = strings.Repeat("a", 40), "initial checks passed"
	cardTask, err = s.ActPeerTask(ctx, "r", act)
	if err != nil {
		t.Fatal(err)
	}
	projected, err := s.ListPeerTasks(ctx, "r")
	if err != nil || len(projected) == 0 || projected[0].ID != cardTask.ID || projected[0].DeliveryID != "one" {
		t.Fatal("submission did not link to its actual native turn", projected, err)
	}
	act.Action, act.Revision, act.Actor, act.Evidence = "reject", cardTask.Revision, "a", "login still fails"
	if _, err = s.ActPeerTask(ctx, "r", act); err != nil {
		t.Fatal(err)
	}
	projected, err = s.ListPeerTasks(ctx, "r")
	if err != nil || len(projected) == 0 || projected[0].DeliveryID != "one" || projected[0].ReviewIssue != act.Evidence || projected[0].ReviewCommit != act.Commit {
		t.Fatal("review did not update the existing card", projected, err)
	}
	if _, err = db.Exec("UPDATE conversation_turns SET state='completed' WHERE id='turn'"); err != nil {
		t.Fatal(err)
	}

	// Simulate a historical submission whose tool action predates delivery IDs.
	if _, err = db.Exec(`UPDATE peer_task_events SET action_json=json_remove(action_json, '$.deliveryId')
		WHERE room_id='r' AND task_id='delivery-card'`); err != nil {
		t.Fatal(err)
	}
	if _, err = db.Exec(`INSERT INTO conversation_activities(id,conversation_id,turn_id,sequence,kind,status,summary,detail_json,created_at,updated_at)
		VALUES('legacy-submit','conv','turn',20,'mcp_tool','completed','Called ao/peer_task',
		json_object('arguments',json_object('action','submit','id','delivery-card','revision',0,'commit',?)),
		CURRENT_TIMESTAMP,CURRENT_TIMESTAMP)`, act.Commit); err != nil {
		t.Fatal(err)
	}
	projected, err = s.ListPeerTasks(ctx, "r")
	if err != nil || projected[0].DeliveryID != "" {
		t.Fatal("unrelated tool revision anchored a task", projected, err)
	}
	if _, err = db.Exec(`UPDATE conversation_activities
		SET detail_json=json_set(detail_json,'$.arguments.revision',1) WHERE id='legacy-submit'`); err != nil {
		t.Fatal(err)
	}
	projected, err = s.ListPeerTasks(ctx, "r")
	if err != nil || projected[0].DeliveryID != "one" || projected[0].ReviewIssue != act.Evidence {
		t.Fatal("legacy task did not resolve to its verified delivery", projected, err)
	}
	// Keep the original execution-attribution fixture focused on its tool row.
	if _, err = db.Exec("DELETE FROM conversation_activities WHERE id='legacy-submit'"); err != nil {
		t.Fatal(err)
	}

	admitted, _, err := s.ReservePeerControl(ctx, "r", "one", "stop-click", "digest", "stop", room.Epoch)
	if err != nil || !admitted {
		t.Fatal(admitted, err)
	}
	if admitted, status, err := s.ReservePeerControl(ctx, "r", "one", "stop-click", "digest", "stop", room.Epoch); err != nil || admitted || status != "uncertain" {
		t.Fatal("crash replay", admitted, status, err)
	}
	if _, _, err := s.ReservePeerControl(ctx, "r", "one", "stop-click", "changed", "stop", room.Epoch); err == nil {
		t.Fatal("changed control payload accepted")
	}
	if err := s.FinishPeerControl(ctx, "r", "one", "stop-click", "stop_requested"); err != nil {
		t.Fatal(err)
	}
	if admitted, status, err := s.ReservePeerControl(ctx, "r", "one", "stop-click", "digest", "stop", room.Epoch); err != nil || admitted || status != "stop_requested" {
		t.Fatal("accepted control replay", admitted, status, err)
	}

	// Execution evidence belongs to this exact native turn, never adjacent history.
	for _, q := range []string{
		"INSERT INTO conversation_activities(id,conversation_id,turn_id,sequence,kind,status,summary,detail_json,created_at,updated_at) VALUES('tool','conv','turn',5,'command','completed','test command','{\"command\":\"pytest\",\"exitCode\":0}',CURRENT_TIMESTAMP,CURRENT_TIMESTAMP)",
		"INSERT INTO conversation_activities(id,conversation_id,turn_id,sequence,kind,status,summary,created_at,updated_at) VALUES('other-tool','conv','unrelated',6,'command','failed','unrelated failure',CURRENT_TIMESTAMP,CURRENT_TIMESTAMP)",
	} {
		if _, err = db.Exec(q); err != nil {
			t.Fatal(err)
		}
	}
	execution, err := s.LoadPeerExecution(ctx, "r", "one")
	if err != nil || execution.SessionID != "b" || execution.TurnID != "turn" || len(execution.Activities) != 1 || execution.Activities[0].ID != "tool" {
		t.Fatalf("wrong execution attribution: %+v %v", execution, err)
	}
	if _, err = s.LoadPeerExecution(ctx, "other", "one"); err == nil {
		t.Fatal("cross-room execution leaked")
	}
	if _, err = db.Exec("UPDATE conversation_activities SET status='failed',revision=revision+1 WHERE id='tool'"); err != nil {
		t.Fatal(err)
	}
	execution, err = s.LoadPeerExecution(ctx, "r", "one")
	if err != nil || len(execution.Activities) != 1 || execution.Activities[0].Status != "failed" {
		t.Fatal("event did not reconcile", execution, err)
	}
	if _, err = db.Exec("DELETE FROM conversation_messages WHERE id='reply'"); err != nil {
		t.Fatal(err)
	}
	if _, err = db.Exec("UPDATE conversation_turns SET state='failed' WHERE id='turn'"); err != nil {
		t.Fatal(err)
	}
	joined, err = s.ListPeerDeliveries(ctx, "r")
	if err != nil || joined[0].TurnState != "failed" || len(joined[0].Replies) != 0 {
		t.Fatal(joined, err)
	}
	if _, err = db.Exec("UPDATE conversation_turns SET rolled_back_at=CURRENT_TIMESTAMP WHERE id='turn'"); err != nil {
		t.Fatal(err)
	}
	joined, err = s.ListPeerDeliveries(ctx, "r")
	if err != nil || len(joined[0].Replies) != 0 {
		t.Fatal("rolled-back reply leaked", joined, err)
	}
	if _, err = s.ListPeerDeliveries(ctx, "missing"); err == nil {
		t.Fatal("missing room reported as empty history")
	}
	_, err = s.ConfigurePeerRoom(ctx, domain.PeerRoom{ID: "other", ProjectID: "p", Members: []string{"a", "b"}, Budget: 2})
	if err != nil {
		t.Fatal(err)
	}
	empty, err := s.ListPeerDeliveries(ctx, "other")
	if err != nil || len(empty) != 0 {
		t.Fatalf("cross-room history: %+v %v", empty, err)
	}

	if _, err = db.Exec("INSERT INTO peer_deliveries(room_id,id,digest,state) VALUES('other','legacy','digest','uncertain')"); err != nil {
		t.Fatal(err)
	}
	legacy, err := s.ListPeerDeliveries(ctx, "other")
	if err != nil || len(legacy) != 1 || legacy[0].Message != "" || legacy[0].Target != "" || legacy[0].State != "uncertain" {
		t.Fatalf("fabricated legacy history: %+v %v", legacy, err)
	}
	if _, err = s.ReservePeerMessage(ctx, "r", msg); err == nil {
		t.Fatal("pending delivery replayed after restart")
	}
	if err = s.FinishPeerMessageDetail(ctx, "r", "one", true, "persisted diagnostic"); err != nil {
		t.Fatal(err)
	}
	history, err = s.ListPeerDeliveries(ctx, "r")
	if err != nil || len(history) != 1 || history[0].State != "accepted" || history[0].Detail != "persisted diagnostic" {
		t.Fatalf("receipt state not updated: %+v %v", history, err)
	}
	if ok, err := s.ReservePeerMessage(ctx, "r", msg); ok || err != nil {
		t.Fatalf("accepted duplicate should not deliver: %v %v", ok, err)
	}
	changed := msg
	changed.Message = "different"
	if _, err = s.ReservePeerMessage(ctx, "r", changed); err == nil {
		t.Fatal("id conflict accepted")
	}
	room.Paused = true
	room, err = s.ConfigurePeerRoom(ctx, room)
	if err != nil {
		t.Fatal(err)
	}
	if _, err = s.ReservePeerMessage(ctx, "r", msg); err == nil {
		t.Fatal("paused room accepted")
	}
	room.Paused = false
	room, err = s.ConfigurePeerRoom(ctx, room)
	if err != nil {
		t.Fatal(err)
	}
	if _, err = s.ReservePeerMessage(ctx, "r", msg); err == nil {
		t.Fatal("old epoch revived")
	}
	msg.Epoch = room.Epoch
	msg.ID = "two"
	if ok, err := s.ReservePeerMessage(ctx, "r", msg); !ok || err != nil {
		t.Fatal(ok, err)
	}
	msg.ID = "three"
	if _, err = s.ReservePeerMessage(ctx, "r", msg); err != nil {
		t.Fatal("legacy room budget blocked unlimited member", err)
	}
	saved, _ := s.GetPeerRoom(ctx, "r")
	if saved.Used != 3 {
		t.Fatal(saved)
	}
	if _, err = s.ConfigurePeerRoom(ctx, domain.PeerRoom{ID: "r", ProjectID: "p", Members: []string{"a", "b"}, Budget: 100}); err == nil {
		t.Fatal("stale overwrite")
	}
	for i, members := range [][]string{{}, {"a", "a"}, {"a", "cross"}} {
		_, err = s.ConfigurePeerRoom(ctx, domain.PeerRoom{ID: fmt.Sprint("bad", i), ProjectID: "p", Members: members, Budget: 2})
		if err == nil {
			t.Fatal("invalid membership")
		}
	}

	task, err := s.CreatePeerTask(ctx, "r", domain.PeerTask{ID: "task", Goal: "Implement a bounded fixture"})
	if err != nil {
		t.Fatal(err)
	}
	winners.Store(0)
	for _, actor := range []string{"a", "b"} {
		wg.Add(1)
		go func(actor string) {
			defer wg.Done()
			_, err := s.ActPeerTask(ctx, "r", domain.PeerTaskAction{ID: task.ID, Actor: actor, Epoch: room.Epoch, Revision: 0, Action: "claim"})
			if err == nil {
				winners.Add(1)
			}
		}(actor)
	}
	wg.Wait()
	if winners.Load() != 1 {
		t.Fatal("claim winners", winners.Load())
	}
	tasks, err := s.ListPeerTasks(ctx, "r")
	if err != nil || len(tasks) != 3 {
		t.Fatal(tasks, err)
	}
	task = tasks[2]
	other := "a"
	if task.Owner == "a" {
		other = "b"
	}
	a := domain.PeerTaskAction{ID: task.ID, Actor: other, Epoch: room.Epoch, Revision: task.Revision, Action: "submit", Evidence: "fixture evidence", Commit: "0123456789012345678901234567890123456789"}
	if _, err = s.ActPeerTask(ctx, "r", a); err == nil {
		t.Fatal("non-owner submitted")
	}
	a.Actor = task.Owner
	task, err = s.ActPeerTask(ctx, "r", a)
	if err != nil {
		t.Fatal(err)
	}
	a.Revision = task.Revision
	a.Action = "accept"
	if _, err = s.ActPeerTask(ctx, "r", a); err == nil {
		t.Fatal("owner self-approved")
	}
	a.Actor = other
	a.Action = "reject"
	a.Evidence = "independent test failed: exit 1"
	task, err = s.ActPeerTask(ctx, "r", a)
	if err != nil || task.State != "claimed" || task.Commit != "" {
		t.Fatal("rejection failed", task, err)
	}
	a.Actor = task.Owner
	a.Revision = task.Revision
	a.Action = "submit"
	a.Commit = "abcdefabcdefabcdefabcdefabcdefabcdefabcd"
	a.Evidence = "fixed; rerun recorded"
	a.Tests = []domain.PeerTestEvidence{{Command: "pytest", Cwd: "/fixture", ExitCode: 0, Scope: "unit tests rerun; browser not verified"}}
	task, err = s.ActPeerTask(ctx, "r", a)
	if err != nil {
		t.Fatal(err)
	}
	a.Actor = other
	a.Revision = task.Revision
	a.Action = "accept"
	a.Evidence = "independent rerun on fixed commit passed"
	task, err = s.ActPeerTask(ctx, "r", a)
	if err != nil || task.State != "accepted" {
		t.Fatal(task, err)
	}
	if _, err = s.ActPeerTask(ctx, "r", a); err == nil {
		t.Fatal("stale review accepted")
	}
	var events int
	if err = db.QueryRow("SELECT count(*) FROM peer_task_events WHERE room_id='r' AND task_id='task'").Scan(&events); err != nil || events != 5 {
		t.Fatal(events, err)
	}

	evidence, err := s.ListPeerTaskEvidence(ctx, "r", task.ID)
	if err != nil || len(evidence) != 5 || evidence[2].Action != "reject" || evidence[2].Commit != "0123456789012345678901234567890123456789" || evidence[4].Commit != task.Commit {
		t.Fatal("lost review revision evidence", evidence, err)
	}
	room.Paused = true
	room, err = s.ConfigurePeerRoom(ctx, room)
	if err != nil {
		t.Fatal(err)
	}
	if _, err = s.GetPeerToolBinding(ctx, "a"); err == nil {
		t.Fatal("paused binding allowed")
	}
	if _, err = s.BindPeerTools(ctx, "r", room.Epoch); err == nil {
		t.Fatal("paused rebind allowed")
	}
	a.Epoch = room.Epoch
	a.Revision = task.Revision
	if _, err = s.ActPeerTask(ctx, "r", a); err == nil {
		t.Fatal("paused task mutation")
	}

	room.Members = nil
	room, err = s.ConfigurePeerRoom(ctx, room)
	if err != nil {
		t.Fatal(err)
	}
	for _, member := range []string{"a", "b"} {
		bound, err := s.PeerSessionBound(ctx, member)
		if err != nil || !bound {
			t.Fatal("removal reopened legacy route", member, err)
		}
	}

	human, err := s.ConfigurePeerRoom(ctx, domain.PeerRoom{ID: "human", ProjectID: "p", Members: []string{"a", "b"}, Budget: 1})
	if err != nil {
		t.Fatal(err)
	}
	hm := domain.PeerMessage{ID: "user-broadcast", Target: "a", Epoch: human.Epoch, Attachments: []domain.PeerAttachment{{Name: "proof.txt", MimeType: "text/plain", Size: 5, SHA256: "abc"}}}
	hm.Message = strings.Repeat("需求与代码\n", 1000)
	tooLarge := hm
	tooLarge.Message = strings.Repeat("x", domain.PeerBroadcastMaxMessageBytes+1)
	if _, err := s.ReserveUserRoomMessage(ctx, "human", tooLarge); err == nil {
		t.Fatal("oversized user message admitted")
	}
	peerLong := hm
	peerLong.Sender = "b"
	peerLong.Attachments = nil
	if _, err := s.ReservePeerMessage(ctx, "human", peerLong); err == nil {
		t.Fatal("peer tool limit was weakened")
	}
	if ok, err := s.ReserveUserRoomMessage(ctx, "human", hm); !ok || err != nil {
		t.Fatal(ok, err)
	}
	if _, err = s.ReserveUserRoomMessage(ctx, "human", hm); err == nil {
		t.Fatal("pending broadcast replay")
	}
	if err = s.FinishPeerMessage(ctx, "human", hm.ID, true); err != nil {
		t.Fatal(err)
	}
	if ok, err := s.ReserveUserRoomMessage(ctx, "human", hm); ok || err != nil {
		t.Fatal("broadcast duplicate", ok, err)
	}
	history, err = s.ListPeerDeliveries(ctx, "human")
	if err != nil || len(history) != 1 || len(history[0].Attachments) != 1 || history[0].Attachments[0] != hm.Attachments[0] {
		t.Fatal("attachment history lost", history, err)
	}
	changed = hm
	changed.Attachments = append([]domain.PeerAttachment(nil), hm.Attachments...)
	changed.Attachments[0].SHA256 = "different-content"
	if _, err = s.ReserveUserRoomMessage(ctx, "human", changed); err == nil {
		t.Fatal("same ID with changed bytes accepted")
	}
	hm.ID = "other"
	hm.Target = "b"
	if _, err = s.ReserveUserRoomMessage(ctx, "human", hm); err != nil {
		t.Fatal("human broadcast blocked by legacy budget", err)
	}
	hm.Sender = "a"
	if _, err = s.ReserveUserRoomMessage(ctx, "human", hm); err == nil {
		t.Fatal("forged human sender")
	}
	limit := 1
	human.MemberSettings = map[string]domain.PeerMemberSettings{"a": {PeerLimit: &limit}}
	human, err = s.ConfigurePeerRoom(ctx, human)
	if err != nil {
		t.Fatal(err)
	}
	origin := domain.PeerMessage{ID: "round-one-input", RoundID: "round-one", BroadcastID: "round-one", Target: "a", Epoch: human.Epoch, Message: "new round"}
	if ok, err := s.ReserveUserRoomMessage(ctx, "human", origin); !ok || err != nil {
		t.Fatal(ok, err)
	}
	if err := s.FinishPeerMessage(ctx, "human", origin.ID, true); err != nil {
		t.Fatal(err)
	}
	peer := domain.PeerMessage{ID: "limited-one", RoundID: "round-one", Sender: "a", Target: "b", Epoch: human.Epoch, Message: "hello"}
	if ok, err := s.ReservePeerMessage(ctx, "human", peer); !ok || err != nil {
		t.Fatal(ok, err)
	}
	peer.ID = "limited-two"
	if _, err := s.ReservePeerMessage(ctx, "human", peer); err == nil {
		t.Fatal("per-member round limit bypassed")
	}
	peer.RoundID = "invented"
	if _, err := s.ReservePeerMessage(ctx, "human", peer); err == nil {
		t.Fatal("invented round accepted")
	}
	origin.ID = "round-two-input"
	origin.RoundID = "round-two"
	origin.BroadcastID = "round-two"
	if ok, err := s.ReserveUserRoomMessage(ctx, "human", origin); !ok || err != nil {
		t.Fatal(ok, err)
	}
	if err := s.FinishPeerMessage(ctx, "human", origin.ID, true); err != nil {
		t.Fatal(err)
	}
	peer.RoundID = "round-two"
	if ok, err := s.ReservePeerMessage(ctx, "human", peer); !ok || err != nil {
		t.Fatal("new round did not renew allowance", ok, err)
	}
	human.MemberSettings["b"] = domain.PeerMemberSettings{Disabled: true}
	human, err = s.ConfigurePeerRoom(ctx, human)
	if err != nil {
		t.Fatal(err)
	}
	origin.ID = "disabled-target"
	origin.Target = "b"
	origin.Epoch = human.Epoch
	if _, err := s.ReserveUserRoomMessage(ctx, "human", origin); err == nil {
		t.Fatal("disabled member received user message")
	}
	peer.ID = "disabled-sender"
	peer.Sender = "b"
	peer.Target = "a"
	peer.Epoch = human.Epoch
	if _, err := s.ReservePeerMessage(ctx, "human", peer); err == nil {
		t.Fatal("disabled member sent peer message")
	}

}

func TestPeerRoomKeepsWorkingWhenAMemberTerminates(t *testing.T) {
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
	for _, sql := range []string{
		"INSERT INTO projects(id,path,display_name,registered_at) VALUES('p','/tmp/p','p',CURRENT_TIMESTAMP)",
		`INSERT INTO sessions(id,project_id,num,kind,activity_state,activity_last_at,created_at,updated_at) VALUES
			('ready','p',1,'worker','idle',CURRENT_TIMESTAMP,CURRENT_TIMESTAMP,CURRENT_TIMESTAMP),
			('expired','p',2,'worker','idle',CURRENT_TIMESTAMP,CURRENT_TIMESTAMP,CURRENT_TIMESTAMP)`,
	} {
		if _, err := db.Exec(sql); err != nil {
			t.Fatal(err)
		}
	}
	room, err := s.ConfigurePeerRoom(ctx, domain.PeerRoom{ID: "room", ProjectID: "p", Members: []string{"ready", "expired"}, Budget: 2})
	if err != nil {
		t.Fatal(err)
	}
	if _, err := s.BindPeerTools(ctx, room.ID, room.Epoch); err != nil {
		t.Fatal(err)
	}
	if _, err := db.Exec("UPDATE sessions SET is_terminated=1 WHERE id='expired'"); err != nil {
		t.Fatal(err)
	}
	room, err = s.ConfigurePeerRoom(ctx, room)
	if err != nil {
		t.Fatal("a retained expired member prevented editing the room", err)
	}
	if _, err := s.BindPeerTools(ctx, room.ID, room.Epoch); err != nil {
		t.Fatal("a retained expired member prevented rebinding ready peers", err)
	}
	if bind, err := s.GetPeerToolBinding(ctx, "ready"); err != nil || bind.Epoch != room.Epoch {
		t.Fatal(bind, err)
	}
	if _, err := s.GetPeerToolBinding(ctx, "expired"); err == nil {
		t.Fatal("expired member retained active authorization")
	}
	if _, err := s.ReserveUserRoomMessage(ctx, room.ID, domain.PeerMessage{ID: "blocked", Target: "expired", Epoch: room.Epoch, Message: "test"}); err == nil {
		t.Fatal("expired member got a new delivery")
	}
	if _, err := s.ConfigurePeerRoom(ctx, domain.PeerRoom{ID: "other", ProjectID: "p", Members: []string{"expired"}, Paused: true, Budget: 2}); err == nil {
		t.Fatal("a new room accepted an expired member")
	}
}
