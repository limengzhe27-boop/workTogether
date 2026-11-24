package store

import (
	"context"
	"database/sql"
	"errors"
	"fmt"
	"slices"

	"github.com/aoagents/agent-orchestrator/backend/internal/domain"
)

// Explicit user action grants the current epoch. Reading a binding never upgrades it.
func (s *Store) BindPeerTools(ctx context.Context, room string, epoch int64) (domain.PeerToolBinding, error) {
	var out domain.PeerToolBinding
	if err := s.writeMu.LockContext(ctx); err != nil {
		return out, err
	}
	defer s.writeMu.Unlock()
	tx, err := s.writeDB.BeginTx(ctx, nil)
	if err != nil {
		return out, err
	}
	defer tx.Rollback()
	r, err := readPeerRoom(ctx, tx, room)
	if err != nil {
		return out, err
	}
	if r.Paused || r.Epoch != epoch {
		return out, fmt.Errorf("paused room or stale epoch")
	}
	for _, id := range r.Members {
		if r.MemberSettings[id].Disabled {
			continue
		}
		if err = peerMember(ctx, tx, id, r.ProjectID); err != nil {
			// A stopped/removed project session must not withhold access from
			// the other eligible members. Its old grant is invalid at this epoch.
			if errors.Is(err, sql.ErrNoRows) || errors.Is(err, errPeerMemberUnavailable) {
				continue
			}
			return out, err
		}
		var prior string
		err = tx.QueryRowContext(ctx, "SELECT room_id FROM peer_tool_grants WHERE session_id=?", id).Scan(&prior)
		if err != nil && err != sql.ErrNoRows {
			return out, err
		}
		if err == nil && prior != room {
			return out, fmt.Errorf("session already bound to another room")
		}
		if _, err = tx.ExecContext(ctx, "INSERT INTO peer_tool_grants(session_id,room_id,epoch) VALUES(?,?,?) ON CONFLICT(session_id) DO UPDATE SET epoch=excluded.epoch", id, room, epoch); err != nil {
			return out, err
		}
	}
	return domain.PeerToolBinding{RoomID: room, ProjectID: r.ProjectID, Epoch: epoch}, tx.Commit()
}
func (s *Store) GetPeerToolBinding(ctx context.Context, id string) (domain.PeerToolBinding, error) {
	var out domain.PeerToolBinding
	err := s.readDB.QueryRowContext(ctx, "SELECT room_id,epoch FROM peer_tool_grants WHERE session_id=?", id).Scan(&out.RoomID, &out.Epoch)
	if err != nil {
		return out, err
	}
	r, err := s.GetPeerRoom(ctx, out.RoomID)
	if err != nil {
		return out, err
	}
	if r.Paused || r.Epoch != out.Epoch || !slices.Contains(r.Members, id) || r.MemberSettings[id].Disabled {
		return out, fmt.Errorf("binding revoked; explicit user rebind required")
	}
	var project string
	var terminated bool
	if err = s.readDB.QueryRowContext(ctx, "SELECT project_id,is_terminated FROM sessions WHERE id=?", id).Scan(&project, &terminated); err != nil {
		return out, err
	}
	if terminated || project != r.ProjectID {
		return out, fmt.Errorf("session unavailable")
	}
	out.ProjectID = r.ProjectID
	return out, nil
}
