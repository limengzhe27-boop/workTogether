package store

import (
	"context"
	"github.com/aoagents/agent-orchestrator/backend/internal/domain"
	"github.com/aoagents/agent-orchestrator/backend/internal/storage/sqlite/gen"
)

// LoadPeerExecution uses the durable delivery key, never text or nearby timestamps.
func (s *Store) LoadPeerExecution(ctx context.Context, room, delivery string) (domain.PeerExecution, error) {
	out := domain.PeerExecution{Activities: []domain.ConversationActivity{}}
	err := s.readDB.QueryRowContext(ctx, `SELECT d.target,d.epoch,t.conversation_id,t.id,t.provider_turn_id,t.state
 FROM peer_deliveries d JOIN conversation_messages u ON u.client_message_id=? AND u.role='user'
 JOIN conversation_turns t ON t.id=u.turn_id AND t.conversation_id=u.conversation_id AND t.handled_by_session_id=d.target
 WHERE d.room_id=? AND d.id=? AND t.rolled_back_at IS NULL AND t.promoted_to_turn_id IS NULL
 ORDER BY u.sequence DESC LIMIT 1`, domain.PeerDeliveryKey(room, delivery), room, delivery).Scan(&out.SessionID, &out.Epoch, &out.ConversationID, &out.TurnID, &out.ProviderTurnID, &out.State)
	if err != nil {
		return out, err
	}
	rows, err := s.readDB.QueryContext(ctx, `SELECT id,conversation_id,turn_id,sequence,revision,kind,status,summary,detail_json,request_id,provider_item_id,created_at,updated_at,command_output,command_output_truncated,streamed_text,streamed_text_truncated
 FROM conversation_activities WHERE conversation_id=? AND turn_id=?
 ORDER BY CASE WHEN kind='approval' AND status='pending' THEN 0 ELSE 1 END, sequence LIMIT 201`, out.ConversationID, out.TurnID)
	if err != nil {
		return out, err
	}
	defer rows.Close()
	for rows.Next() {
		var a gen.ConversationActivity
		if err := rows.Scan(&a.ID, &a.ConversationID, &a.TurnID, &a.Sequence, &a.Revision, &a.Kind, &a.Status, &a.Summary, &a.DetailJson, &a.RequestID, &a.ProviderItemID, &a.CreatedAt, &a.UpdatedAt, &a.CommandOutput, &a.CommandOutputTruncated, &a.StreamedText, &a.StreamedTextTruncated); err != nil {
			return out, err
		}
		out.Activities = append(out.Activities, activityToDomain(a))
	}
	if err := rows.Err(); err != nil {
		return out, err
	}
	out.Truncated = len(out.Activities) > 200
	if out.Truncated {
		out.Activities = out.Activities[:200]
	}
	return out, nil
}
