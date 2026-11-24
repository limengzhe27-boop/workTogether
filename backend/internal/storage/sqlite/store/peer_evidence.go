package store

import (
	"context"
	"database/sql"
	"encoding/json"
	"fmt"
	"github.com/aoagents/agent-orchestrator/backend/internal/domain"
	"slices"
	"strings"
	"time"
)

func (s *Store) RecordPeerCandidate(ctx context.Context, room string, c domain.PeerCandidate) error {
	raw, err := json.Marshal(c.Tasks)
	if err != nil {
		return err
	}
	_, err = s.writeDB.ExecContext(ctx, `INSERT INTO peer_integration_candidates(room_id,candidate_commit,branch,tasks_json,created_at) VALUES(?,?,?,?,?) ON CONFLICT(room_id,candidate_commit) DO NOTHING`, room, c.Commit, c.Branch, string(raw), time.Now().UTC().Format(time.RFC3339Nano))
	return err
}
func (s *Store) ListPeerCandidates(ctx context.Context, room string) ([]domain.PeerCandidate, error) {
	rows, err := s.readDB.QueryContext(ctx, `SELECT candidate_commit,branch,tasks_json,created_at FROM peer_integration_candidates WHERE room_id=? ORDER BY created_at`, room)
	if err != nil {
		return nil, err
	}
	out := []domain.PeerCandidate{}
	for rows.Next() {
		var c domain.PeerCandidate
		var raw string
		if err = rows.Scan(&c.Commit, &c.Branch, &raw, &c.CreatedAt); err != nil {
			rows.Close()
			return nil, err
		}
		if err = json.Unmarshal([]byte(raw), &c.Tasks); err != nil {
			rows.Close()
			return nil, err
		}
		c.Checks = []domain.PeerIntegrationCheck{}
		out = append(out, c)
	}
	err = rows.Err()
	rows.Close()
	if err != nil {
		return nil, err
	}
	for i := range out {
		checks, err := s.readDB.QueryContext(ctx, `SELECT id,executor,command,cwd,exit_code,scope,created_at FROM peer_integration_checks WHERE room_id=? AND candidate_commit=? ORDER BY created_at`, room, out[i].Commit)
		if err != nil {
			return nil, err
		}
		for checks.Next() {
			c := domain.PeerIntegrationCheck{Commit: out[i].Commit}
			if err = checks.Scan(&c.ID, &c.Executor, &c.Command, &c.Cwd, &c.ExitCode, &c.Scope, &c.CreatedAt); err != nil {
				checks.Close()
				return nil, err
			}
			out[i].Checks = append(out[i].Checks, c)
		}
		err = checks.Err()
		checks.Close()
		if err != nil {
			return nil, err
		}
	}
	return out, nil
}
func (s *Store) RecordPeerIntegrationCheck(ctx context.Context, room string, c domain.PeerIntegrationCheck) error {
	if len(c.ID) == 0 || len(c.ID) > 100 || len(c.Commit) != 40 || strings.TrimSpace(c.Executor) == "" || len(c.Executor) > 200 || strings.TrimSpace(c.Command) == "" || len(c.Command) > 2000 || strings.TrimSpace(c.Cwd) == "" || len(c.Cwd) > 2000 || strings.TrimSpace(c.Scope) == "" || len(c.Scope) > 4000 {
		return fmt.Errorf("测试记录需包含执行人、固定提交、命令、目录和验收范围")
	}
	if err := s.writeMu.LockContext(ctx); err != nil {
		return err
	}
	defer s.writeMu.Unlock()
	tx, err := s.writeDB.BeginTx(ctx, nil)
	if err != nil {
		return err
	}
	defer tx.Rollback()
	r, err := readPeerRoom(ctx, tx, room)
	if err != nil {
		return err
	}
	if r.Epoch != c.Epoch {
		return fmt.Errorf("房间已变化")
	}
	_, err = tx.ExecContext(ctx, `INSERT INTO peer_integration_checks(room_id,id,candidate_commit,executor,command,cwd,exit_code,scope,created_at) VALUES(?,?,?,?,?,?,?,?,?)`, room, c.ID, c.Commit, c.Executor, c.Command, c.Cwd, c.ExitCode, c.Scope, time.Now().UTC().Format(time.RFC3339Nano))
	if err != nil {
		return err
	}
	return tx.Commit()
}

// Task actions remain immutable and identify the fixed commit reviewed at each revision.
func (s *Store) ListPeerTaskEvidence(ctx context.Context, room, task string) ([]domain.PeerTaskAction, error) {
	rows, err := s.readDB.QueryContext(ctx, `SELECT action_json FROM peer_task_events WHERE room_id=? AND task_id=? ORDER BY revision`, room, task)
	if err != nil {
		return nil, err
	}
	defer rows.Close()
	out := []domain.PeerTaskAction{}
	for rows.Next() {
		var raw string
		var a domain.PeerTaskAction
		if err = rows.Scan(&raw); err != nil {
			return nil, err
		}
		if err = json.Unmarshal([]byte(raw), &a); err != nil {
			return nil, err
		}
		out = append(out, a)
	}
	return out, rows.Err()
}

func (s *Store) ReservePeerControl(ctx context.Context, room, delivery, id, digest, action string, epoch int64) (bool, string, error) {
	if err := s.writeMu.LockContext(ctx); err != nil {
		return false, "", err
	}
	defer s.writeMu.Unlock()
	tx, err := s.writeDB.BeginTx(ctx, nil)
	if err != nil {
		return false, "", err
	}
	defer tx.Rollback()
	r, err := readPeerRoom(ctx, tx, room)
	if err != nil {
		return false, "", err
	}
	var target string
	var deliveryEpoch int64
	if err = tx.QueryRowContext(ctx, "SELECT target,epoch FROM peer_deliveries WHERE room_id=? AND id=?", room, delivery).Scan(&target, &deliveryEpoch); err != nil {
		return false, "", err
	}
	if r.Epoch != epoch || !slices.Contains(r.Members, target) || (action == "steer" && (deliveryEpoch != epoch || r.Paused || r.MemberSettings[target].Disabled)) {
		return false, "", fmt.Errorf("房间或成员授权已变化")
	}
	var old, status string
	err = tx.QueryRowContext(ctx, "SELECT digest,status FROM peer_execution_controls WHERE room_id=? AND delivery_id=? AND id=?", room, delivery, id).Scan(&old, &status)
	if err == nil {
		if old != digest {
			return false, "", fmt.Errorf("操作编号对应其他内容")
		}
		return false, status, nil
	}
	if err != sql.ErrNoRows {
		return false, "", err
	}
	_, err = tx.ExecContext(ctx, "INSERT INTO peer_execution_controls(room_id,delivery_id,id,digest,status) VALUES(?,?,?,?,'uncertain')", room, delivery, id, digest)
	if err != nil {
		return false, "", err
	}
	return true, "", tx.Commit()
}
func (s *Store) FinishPeerControl(ctx context.Context, room, delivery, id, status string) error {
	_, err := s.writeDB.ExecContext(ctx, "UPDATE peer_execution_controls SET status=? WHERE room_id=? AND delivery_id=? AND id=? AND status='uncertain'", status, room, delivery, id)
	return err
}
