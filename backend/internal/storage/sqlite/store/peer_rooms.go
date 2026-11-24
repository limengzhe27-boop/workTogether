package store

import (
	"context"
	"crypto/sha256"
	"database/sql"
	"encoding/json"
	"errors"
	"fmt"
	"slices"
	"strings"
	"time"

	"github.com/aoagents/agent-orchestrator/backend/internal/domain"
)

func readPeerRoom(ctx context.Context, q interface {
	QueryRowContext(context.Context, string, ...any) *sql.Row
}, id string) (domain.PeerRoom, error) {
	var r domain.PeerRoom
	var members, settings string
	err := q.QueryRowContext(ctx, "SELECT id,project_id,members,epoch,budget,used,paused,member_settings FROM peer_rooms WHERE id=?", id).Scan(&r.ID, &r.ProjectID, &members, &r.Epoch, &r.Budget, &r.Used, &r.Paused, &settings)
	if err == nil {
		err = json.Unmarshal([]byte(members), &r.Members)
		if err == nil {
			err = json.Unmarshal([]byte(settings), &r.MemberSettings)
		}
	}
	return r, err
}
func (s *Store) GetPeerRoom(ctx context.Context, id string) (domain.PeerRoom, error) {
	return readPeerRoom(ctx, s.readDB, id)
}

var errPeerMemberUnavailable = errors.New("member is outside project or terminated")

func peerMember(ctx context.Context, tx *sql.Tx, id, project string) error {
	var actual string
	var terminated bool
	if err := tx.QueryRowContext(ctx, "SELECT project_id,is_terminated FROM sessions WHERE id=?", id).Scan(&actual, &terminated); err != nil {
		return err
	}
	if actual != project || terminated {
		return errPeerMemberUnavailable
	}
	return nil
}

// Updates revoke old epochs and never reset usage.
func (s *Store) ConfigurePeerRoom(ctx context.Context, r domain.PeerRoom) (domain.PeerRoom, error) {
	if r.ID == "" || len(r.ID) > 100 || r.ProjectID == "" || r.Budget < 1 || r.Budget > 100 || (!r.Paused && len(r.Members) == 0) || len(r.Members) > 16 {
		return r, fmt.Errorf("invalid room configuration")
	}
	if err := s.writeMu.LockContext(ctx); err != nil {
		return r, err
	}
	defer s.writeMu.Unlock()
	tx, err := s.writeDB.BeginTx(ctx, nil)
	if err != nil {
		return r, err
	}
	defer tx.Rollback()
	old, err := readPeerRoom(ctx, tx, r.ID)
	if err != nil && err != sql.ErrNoRows {
		return r, err
	}
	if err == sql.ErrNoRows {
		if r.Epoch != 0 {
			return r, fmt.Errorf("stale room epoch")
		}
		r.Used = 0
	} else {
		if old.Epoch != r.Epoch || old.ProjectID != r.ProjectID {
			return r, fmt.Errorf("stale epoch or changed project")
		}
		r.Used = old.Used
	}
	seen := map[string]bool{}
	for _, id := range r.Members {
		if id == "" || seen[id] {
			return r, fmt.Errorf("duplicate or empty member")
		}
		seen[id] = true
		if err = peerMember(ctx, tx, id, r.ProjectID); err != nil {
			// Keep stale entries visible for cleanup. Only eligible sessions may
			// be added, and every delivery still revalidates eligibility.
			if !slices.Contains(old.Members, id) || (!errors.Is(err, sql.ErrNoRows) && !errors.Is(err, errPeerMemberUnavailable)) {
				return r, err
			}
		}
	}
	for id, setting := range r.MemberSettings {
		if !seen[id] || (setting.PeerLimit != nil && (*setting.PeerLimit < 0 || *setting.PeerLimit > 10000)) {
			return r, fmt.Errorf("invalid member settings")
		}
	}
	r.Epoch++
	members, _ := json.Marshal(r.Members)
	settings, _ := json.Marshal(r.MemberSettings)
	_, err = tx.ExecContext(ctx, "INSERT INTO peer_rooms(id,project_id,members,epoch,budget,used,paused,member_settings) VALUES(?,?,?,?,?,?,?,?) ON CONFLICT(id) DO UPDATE SET members=excluded.members,epoch=excluded.epoch,budget=excluded.budget,paused=excluded.paused,member_settings=excluded.member_settings", r.ID, r.ProjectID, string(members), r.Epoch, r.Budget, r.Used, r.Paused, string(settings))
	if err != nil {
		return r, err
	}
	for _, id := range r.Members {
		if _, err = tx.ExecContext(ctx, "INSERT OR IGNORE INTO peer_session_bindings(session_id) VALUES(?)", id); err != nil {
			return r, err
		}
	}
	return r, tx.Commit()
}

// Reservation precedes external delivery; crashes never automatically replay.
func (s *Store) ReservePeerMessage(ctx context.Context, room string, m domain.PeerMessage) (bool, error) {
	return s.reservePeerMessage(ctx, room, m, false)
}

// Only the trusted user broadcast service calls this; it is not a peer tool parameter.
func (s *Store) ReserveUserRoomMessage(ctx context.Context, room string, m domain.PeerMessage) (bool, error) {
	if m.Sender != "" {
		return false, fmt.Errorf("user broadcast cannot impersonate a session")
	}
	return s.reservePeerMessage(ctx, room, m, true)
}
func (s *Store) reservePeerMessage(ctx context.Context, room string, m domain.PeerMessage, human bool) (bool, error) {
	if !human && len(m.Attachments) > 0 {
		return false, fmt.Errorf("peer attachments require a user broadcast")
	}
	messageLimit := 2000
	if human {
		messageLimit = domain.PeerBroadcastMaxMessageBytes
	}
	if m.ID == "" || len(m.ID) > 100 || m.Sender == m.Target || (strings.TrimSpace(m.Message) == "" && !(human && len(m.Attachments) > 0)) || len(m.Message) > messageLimit {
		return false, fmt.Errorf("invalid peer message")
	}
	if err := s.writeMu.LockContext(ctx); err != nil {
		return false, err
	}
	defer s.writeMu.Unlock()
	tx, err := s.writeDB.BeginTx(ctx, nil)
	if err != nil {
		return false, err
	}
	defer tx.Rollback()
	r, err := readPeerRoom(ctx, tx, room)
	if err != nil {
		return false, err
	}
	if r.Paused || m.Epoch != r.Epoch || (!human && !slices.Contains(r.Members, m.Sender)) || !slices.Contains(r.Members, m.Target) {
		return false, fmt.Errorf("room paused, stale epoch or non-member")
	}
	if r.MemberSettings[m.Target].Disabled || (!human && r.MemberSettings[m.Sender].Disabled) {
		return false, fmt.Errorf("member disabled")
	}
	ids := []string{m.Target}
	if !human {
		ids = append(ids, m.Sender)
	}
	for _, id := range ids {
		if err = peerMember(ctx, tx, id, r.ProjectID); err != nil {
			return false, err
		}
	}
	raw, _ := json.Marshal(m)
	digest := fmt.Sprintf("%x", sha256.Sum256(raw))
	var prior, state string
	err = tx.QueryRowContext(ctx, "SELECT digest,state FROM peer_deliveries WHERE room_id=? AND id=?", room, m.ID).Scan(&prior, &state)
	if err == nil {
		if prior != digest {
			return false, fmt.Errorf("message id reused with different content")
		}
		if state != "accepted" {
			return false, fmt.Errorf("delivery unresolved; do not replay")
		}
		return false, nil
	}
	if err != sql.ErrNoRows {
		return false, err
	}
	if !human {
		limit := r.MemberSettings[m.Sender].PeerLimit
		if limit != nil && *limit == 0 {
			return false, fmt.Errorf("主动联系同伴已关闭")
		}
		if m.RoundID != "" || limit != nil {
			var count int
			if m.RoundID == "" {
				return false, fmt.Errorf("roundId required for limited peer messages")
			}
			err = tx.QueryRowContext(ctx, "SELECT count(*) FROM peer_deliveries WHERE room_id=? AND round_id=? AND target=? AND epoch=? AND state='accepted'", room, m.RoundID, m.Sender, r.Epoch).Scan(&count)
			if err != nil {
				return false, err
			}
			if count == 0 {
				return false, fmt.Errorf("unknown or stale round for sender")
			}
			if limit != nil {
				err = tx.QueryRowContext(ctx, "SELECT count(*) FROM peer_deliveries WHERE room_id=? AND round_id=? AND sender=?", room, m.RoundID, m.Sender).Scan(&count)
				if err != nil {
					return false, err
				}
				if count >= *limit {
					return false, fmt.Errorf("本轮主动联系同伴次数已用完")
				}
			}
		}
	}
	attachments, _ := json.Marshal(m.Attachments)
	if _, err = tx.ExecContext(ctx, "INSERT INTO peer_deliveries(room_id,id,digest,state,sender,target,message,epoch,created_at,broadcast_id,attachments,round_id) VALUES(?,?,?,'pending',?,?,?,?,?,?,?,?)", room, m.ID, digest, m.Sender, m.Target, m.Message, m.Epoch, time.Now().UTC().Format(time.RFC3339Nano), m.BroadcastID, string(attachments), m.RoundID); err != nil {
		return false, err
	}
	if _, err = tx.ExecContext(ctx, "UPDATE peer_rooms SET used=used+1 WHERE id=?", room); err != nil {
		return false, err
	}
	return true, tx.Commit()
}
func (s *Store) FinishPeerMessage(ctx context.Context, room, id string, accepted bool) error {
	return s.FinishPeerMessageDetail(ctx, room, id, accepted, "")
}
func (s *Store) FinishPeerMessageDetail(ctx context.Context, room, id string, accepted bool, detail string) error {
	state := "uncertain"
	if accepted {
		state = "accepted"
	}
	_, err := s.writeDB.ExecContext(ctx, "UPDATE peer_deliveries SET state=?,detail=? WHERE room_id=? AND id=? AND state='pending'", state, detail, room, id)
	return err
}

// Binding history is sticky: removing a member or pausing cannot reopen a legacy channel.
func (s *Store) PeerSessionBound(ctx context.Context, id string) (bool, error) {
	var n int
	err := s.readDB.QueryRowContext(ctx, "SELECT count(*) FROM peer_session_bindings WHERE session_id=?", id).Scan(&n)
	return n > 0, err
}

// Preserve history independently of the per-member admission policy.
func (s *Store) ListPeerDeliveries(ctx context.Context, room string) ([]domain.PeerDelivery, error) {
	if _, err := s.GetPeerRoom(ctx, room); err != nil {
		return nil, err
	}
	rows, err := s.readDB.QueryContext(ctx, "SELECT id,sender,target,message,epoch,state,created_at,detail,broadcast_id,attachments FROM peer_deliveries WHERE room_id=? ORDER BY created_at,id", room)
	if err != nil {
		return nil, err
	}
	defer rows.Close()
	out := []domain.PeerDelivery{}
	for rows.Next() {
		var d domain.PeerDelivery
		var attachments string
		if err := rows.Scan(&d.ID, &d.Sender, &d.Target, &d.Message, &d.Epoch, &d.State, &d.CreatedAt, &d.Detail, &d.BroadcastID, &attachments); err != nil {
			return nil, err
		}
		if err := json.Unmarshal([]byte(attachments), &d.Attachments); err != nil {
			return nil, err
		}
		out = append(out, d)
	}
	if err := rows.Err(); err != nil {
		return nil, err
	}
	rows.Close()
	if err := s.loadPeerReplies(ctx, room, out); err != nil {
		return nil, err
	}
	if err := s.loadPeerGeneratedImages(ctx, room, out); err != nil {
		return nil, err
	}
	return out, nil
}

// Match replies by their durable client input key and handling session. A single
// read keeps each reply and its turn state coherent even while providers stream.
func (s *Store) loadPeerReplies(ctx context.Context, room string, deliveries []domain.PeerDelivery) error {
	if len(deliveries) == 0 {
		return nil
	}
	keys := make([][2]string, len(deliveries))
	for i := range deliveries {
		keys[i] = [2]string{domain.PeerDeliveryKey(room, deliveries[i].ID), deliveries[i].Target}
		deliveries[i].Replies = []domain.PeerReply{}
	}
	input, err := json.Marshal(keys)
	if err != nil {
		return err
	}
	rows, err := s.readDB.QueryContext(ctx, `
		WITH inputs AS (
			SELECT CAST(key AS INTEGER) AS idx, json_extract(value,'$[0]') AS client_key, json_extract(value,'$[1]') AS target
			FROM json_each(?)
		), resolved AS (
			SELECT k.idx,t.id,t.conversation_id,t.state,
				row_number() OVER(PARTITION BY k.idx ORDER BY u.sequence DESC,t.id) AS choice
			FROM conversation_messages u JOIN inputs k ON u.client_message_id=k.client_key
			JOIN conversation_turns t ON t.id=u.turn_id AND t.conversation_id=u.conversation_id
			WHERE u.role='user' AND t.handled_by_session_id=k.target
				AND t.rolled_back_at IS NULL AND t.promoted_to_turn_id IS NULL
		), replies AS (
			SELECT r.idx,a.id,a.text,a.streaming,a.sequence,a.created_at,
				row_number() OVER(PARTITION BY r.idx ORDER BY a.sequence DESC) AS newest
			FROM resolved r
			JOIN conversation_messages a ON a.conversation_id=r.conversation_id AND a.turn_id=r.id
			WHERE r.choice=1 AND a.role='assistant'
		)
		SELECT r.idx,r.state,a.id,a.text,a.streaming,a.created_at
		FROM resolved r LEFT JOIN replies a ON a.idx=r.idx AND a.newest<=101
		WHERE r.choice=1
		ORDER BY r.idx,a.sequence DESC`, string(input))
	if err != nil {
		return err
	}
	defer rows.Close()
	for rows.Next() {
		var index int
		var state string
		var id, text sql.NullString
		var streaming sql.NullBool
		var at sql.NullTime
		if err := rows.Scan(&index, &state, &id, &text, &streaming, &at); err != nil {
			return err
		}
		delivery := &deliveries[index]
		delivery.TurnState = state
		if !id.Valid {
			continue
		}
		if len(delivery.Replies) == 100 {
			delivery.RepliesTruncated = true
			continue
		}
		delivery.Replies = append(delivery.Replies, domain.PeerReply{
			ID: id.String, Text: text.String, Streaming: streaming.Bool, TurnState: state,
			CreatedAt: at.Time.UTC().Format(time.RFC3339Nano),
		})
	}
	if err := rows.Err(); err != nil {
		return err
	}
	for i := range deliveries {
		slices.Reverse(deliveries[i].Replies)
	}
	return nil
}
