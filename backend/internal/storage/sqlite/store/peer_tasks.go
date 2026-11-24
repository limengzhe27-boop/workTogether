package store

import (
	"context"
	"database/sql"
	"encoding/hex"
	"encoding/json"
	"fmt"
	"slices"
	"strings"

	"github.com/aoagents/agent-orchestrator/backend/internal/domain"
)

func (s *Store) ListPeerTasks(ctx context.Context, room string) ([]domain.PeerTask, error) {
	rows, err := s.readDB.QueryContext(ctx, `
		SELECT t.id,t.goal,t.state,t.owner,t.revision,t.evidence,t.candidate_commit,CASE WHEN t.state='submitted' THEN '' ELSE t.reviewer END,
		  COALESCE((SELECT NULLIF(json_extract(e.action_json,'$.deliveryId'),'')
		    FROM peer_task_events e WHERE e.room_id=t.room_id AND e.task_id=t.id
		      AND json_extract(e.action_json,'$.action')='submit'
		      AND NULLIF(json_extract(e.action_json,'$.deliveryId'),'') IS NOT NULL
		    ORDER BY e.revision LIMIT 1),''),
		  COALESCE((SELECT substr(json_extract(e.action_json,'$.evidence'),1,200)
		    FROM peer_task_events e WHERE e.room_id=t.room_id AND e.task_id=t.id AND e.revision=t.revision
		      AND json_extract(e.action_json,'$.action')='reject'),''),
		  COALESCE((SELECT json_extract(e.action_json,'$.commit')
		    FROM peer_task_events e WHERE e.room_id=t.room_id AND e.task_id=t.id AND e.revision=t.revision
		      AND json_extract(e.action_json,'$.action') IN ('accept','reject')),'')
		FROM peer_tasks t WHERE t.room_id=? ORDER BY t.id`, room)
	if err != nil {
		return nil, err
	}
	defer rows.Close()
	result := []domain.PeerTask{}
	for rows.Next() {
		var t domain.PeerTask
		if err = rows.Scan(&t.ID, &t.Goal, &t.State, &t.Owner, &t.Revision, &t.Evidence, &t.Commit, &t.Reviewer, &t.DeliveryID, &t.ReviewIssue, &t.ReviewCommit); err != nil {
			return nil, err
		}
		result = append(result, t)
	}
	if err := rows.Err(); err != nil {
		return nil, err
	}
	rows.Close()

	var legacy []string
	for _, task := range result {
		if task.DeliveryID == "" && (task.Commit != "" || task.ReviewIssue != "") {
			legacy = append(legacy, task.ID)
		}
	}
	if len(legacy) > 0 {
		links, err := s.legacyTaskDeliveryLinks(ctx, room, legacy)
		if err != nil {
			return nil, err
		}
		for i := range result {
			if result[i].DeliveryID == "" {
				result[i].DeliveryID = links[result[i].ID]
			}
		}
	}
	return result, nil
}

func (s *Store) CreatePeerTask(ctx context.Context, room string, t domain.PeerTask) (domain.PeerTask, error) {
	if t.ID == "" || len(t.ID) > 100 || strings.TrimSpace(t.Goal) == "" || len(t.Goal) > 4000 {
		return t, fmt.Errorf("invalid task")
	}
	if err := s.writeMu.LockContext(ctx); err != nil {
		return t, err
	}
	defer s.writeMu.Unlock()
	tx, err := s.writeDB.BeginTx(ctx, nil)
	if err != nil {
		return t, err
	}
	defer tx.Rollback()
	if _, err = readPeerRoom(ctx, tx, room); err != nil {
		return t, err
	}
	var count int
	if err = tx.QueryRowContext(ctx, "SELECT count(*) FROM peer_tasks WHERE room_id=?", room).Scan(&count); err != nil {
		return t, err
	}
	if count >= 100 {
		return t, fmt.Errorf("room task limit reached")
	}
	t = domain.PeerTask{ID: t.ID, Goal: t.Goal, State: "open"}
	if _, err = tx.ExecContext(ctx, "INSERT INTO peer_tasks(room_id,id,goal,state) VALUES(?,?,?,'open')", room, t.ID, t.Goal); err != nil {
		return t, err
	}
	return t, tx.Commit()
}
func (s *Store) ActPeerTask(ctx context.Context, room string, a domain.PeerTaskAction) (domain.PeerTask, error) {
	if a.Action == "release" {
		return domain.PeerTask{}, fmt.Errorf("only the user can release a claim")
	}
	return s.actPeerTask(ctx, room, a)
}

func (s *Store) ReleasePeerTask(ctx context.Context, room, task string, epoch, revision int64) (domain.PeerTask, error) {
	return s.actPeerTask(ctx, room, domain.PeerTaskAction{
		ID: task, Epoch: epoch, Revision: revision, Action: "release",
	})
}

func (s *Store) actPeerTask(ctx context.Context, room string, a domain.PeerTaskAction) (domain.PeerTask, error) {
	var t domain.PeerTask
	a.DeliveryID = ""
	if len(a.Evidence) > 4000 || len(a.Commit) > 40 || len(a.Goal) > 4000 || a.ID == "" || len(a.ID) > 100 {
		return t, fmt.Errorf("oversized task evidence")
	}

	rawEvidence, _ := json.Marshal(a)
	if len(rawEvidence) > 16000 || len(a.Files) > 100 || len(a.Tests) > 30 || len(a.Executions) > 20 {
		return t, fmt.Errorf("structured evidence too large")
	}
	for _, check := range a.Tests {
		if strings.TrimSpace(check.Command) == "" || strings.TrimSpace(check.Cwd) == "" || strings.TrimSpace(check.Scope) == "" {
			return t, fmt.Errorf("test evidence requires command, directory and verified/unverified scope")
		}
	}
	for _, delivery := range a.Executions {
		run, err := s.LoadPeerExecution(ctx, room, delivery)
		if err != nil || run.SessionID != a.Actor {
			return t, fmt.Errorf("execution evidence must belong to the submitting member and room")
		}
	}
	if err := s.writeMu.LockContext(ctx); err != nil {
		return t, err
	}
	defer s.writeMu.Unlock()
	tx, err := s.writeDB.BeginTx(ctx, nil)
	if err != nil {
		return t, err
	}
	defer tx.Rollback()
	r, err := readPeerRoom(ctx, tx, room)
	if err != nil {
		return t, err
	}
	if a.Epoch != r.Epoch {
		return t, fmt.Errorf("room membership changed")
	}
	if a.Action != "release" {
		if r.Paused || !slices.Contains(r.Members, a.Actor) || r.MemberSettings[a.Actor].Disabled {
			return t, fmt.Errorf("paused room, disabled member or non-member")
		}
		if err = peerMember(ctx, tx, a.Actor, r.ProjectID); err != nil {
			return t, err
		}
	}

	if a.Action == "propose" {
		if a.Revision != 0 || strings.TrimSpace(a.Goal) == "" {
			return t, fmt.Errorf("new task needs goal and revision zero")
		}
		var count int
		if err = tx.QueryRowContext(ctx, "SELECT count(*) FROM peer_tasks WHERE room_id=?", room).Scan(&count); err != nil {
			return t, err
		}
		if count >= 100 {
			return t, fmt.Errorf("room task limit reached")
		}
		t = domain.PeerTask{ID: a.ID, Goal: a.Goal, State: "open"}
		if _, err = tx.ExecContext(ctx, "INSERT INTO peer_tasks(room_id,id,goal,state) VALUES(?,?,?,'open')", room, t.ID, t.Goal); err != nil {
			return t, err
		}
		raw, _ := json.Marshal(a)
		if _, err = tx.ExecContext(ctx, "INSERT INTO peer_task_events(room_id,task_id,revision,action_json) VALUES(?,?,0,?)", room, t.ID, string(raw)); err != nil {
			return t, err
		}
		return t, tx.Commit()
	}
	if err = tx.QueryRowContext(ctx, "SELECT id,goal,state,owner,revision,evidence,candidate_commit,reviewer FROM peer_tasks WHERE room_id=? AND id=?", room, a.ID).Scan(&t.ID, &t.Goal, &t.State, &t.Owner, &t.Revision, &t.Evidence, &t.Commit, &t.Reviewer); err != nil {
		return t, err
	}
	if a.Revision != t.Revision {
		return t, fmt.Errorf("task changed; read current revision before acting")
	}
	switch a.Action {
	case "claim":
		if t.State != "open" {
			return t, fmt.Errorf("task already claimed")
		}
		t.Owner = a.Actor
		t.State = "claimed"
	case "submit":
		if t.State != "claimed" || t.Owner != a.Actor || strings.TrimSpace(a.Evidence) == "" {
			return t, fmt.Errorf("only owner can submit with evidence")
		}
		if len(a.Commit) != 40 {
			return t, fmt.Errorf("candidate commit required")
		}
		if _, err = hex.DecodeString(a.Commit); err != nil {
			return t, err
		}
		a.DeliveryID, err = activeTaskDelivery(ctx, tx, room, a.Actor, a.Epoch)
		if err != nil {
			return t, err
		}
		t.State = "submitted"
		t.Commit = a.Commit
		t.Evidence = a.Evidence
		t.Reviewer = ""
	case "accept", "reject":
		if t.State != "submitted" || t.Owner == a.Actor || strings.TrimSpace(a.Evidence) == "" {
			return t, fmt.Errorf("independent reviewer and evidence required")
		}
		a.DeliveryID, err = activeTaskDelivery(ctx, tx, room, a.Actor, a.Epoch)
		if err != nil {
			return t, err
		}
		a.Commit = t.Commit // Bind the review event to the exact submitted revision.
		t.Reviewer = a.Actor
		t.Evidence += "\nReview: " + a.Evidence
		if a.Action == "accept" {
			t.State = "accepted"
		} else {
			t.State = "claimed"
			t.Commit = ""
		}
	case "release":
		if t.State != "claimed" {
			return t, fmt.Errorf("only a claimed task can be returned to the queue")
		}
		t.State, t.Owner, t.Evidence, t.Commit, t.Reviewer = "open", "", "", "", ""
	default:
		return t, fmt.Errorf("unknown task action")
	}
	t.Revision++
	_, err = tx.ExecContext(ctx, "UPDATE peer_tasks SET state=?,owner=?,revision=?,evidence=?,candidate_commit=?,reviewer=? WHERE room_id=? AND id=?", t.State, t.Owner, t.Revision, t.Evidence, t.Commit, t.Reviewer, room, t.ID)
	if err != nil {
		return t, err
	}
	raw, _ := json.Marshal(a)
	if _, err = tx.ExecContext(ctx, "INSERT INTO peer_task_events(room_id,task_id,revision,action_json) VALUES(?,?,?,?)", room, t.ID, t.Revision, string(raw)); err != nil {
		return t, err
	}
	return t, tx.Commit()
}

// Use the persisted native input key to link a submission to the current room turn.
// Ambiguous or unavailable linkage leaves the task in the task panel.
func activeTaskDelivery(ctx context.Context, tx *sql.Tx, room, actor string, epoch int64) (string, error) {
	rows, err := tx.QueryContext(ctx, `
		SELECT DISTINCT u.client_message_id FROM conversation_turns t
		JOIN conversation_messages u ON u.turn_id=t.id AND u.conversation_id=t.conversation_id
		WHERE t.handled_by_session_id=? AND t.state='running' AND u.role='user'
		  AND u.client_message_id LIKE 'peer:%' AND t.rolled_back_at IS NULL AND t.promoted_to_turn_id IS NULL
		LIMIT 2`, actor)
	if err != nil {
		return "", err
	}
	var keys []string
	for rows.Next() {
		var key string
		if err := rows.Scan(&key); err != nil {
			rows.Close()
			return "", err
		}
		keys = append(keys, key)
	}
	err = rows.Err()
	rows.Close()
	if err != nil || len(keys) != 1 {
		return "", err
	}
	deliveries, err := tx.QueryContext(ctx, `SELECT id FROM peer_deliveries WHERE room_id=? AND target=? AND epoch=?`, room, actor, epoch)
	if err != nil {
		return "", err
	}
	defer deliveries.Close()
	for deliveries.Next() {
		var id string
		if err := deliveries.Scan(&id); err != nil {
			return "", err
		}
		if domain.PeerDeliveryKey(room, id) == keys[0] {
			return id, nil
		}
	}
	return "", deliveries.Err()
}
