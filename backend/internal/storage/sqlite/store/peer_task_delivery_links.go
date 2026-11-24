package store

import (
	"context"
	"encoding/json"

	"github.com/aoagents/agent-orchestrator/backend/internal/domain"
)

// Older submissions predate persisted delivery IDs. Match the completed tool
// call to the original task event; then resolve the native turn's input key
// against deliveries in this room. A matching task name or nearby reply is
// insufficient because the same member can submit repeatedly.
func (s *Store) legacyTaskDeliveryLinks(ctx context.Context, room string, taskIDs []string) (map[string]string, error) {
	ids, err := json.Marshal(taskIDs)
	if err != nil {
		return nil, err
	}
	type delivery struct {
		id, actor string
		epoch     int64
	}
	rows, err := s.readDB.QueryContext(ctx, `SELECT id,target,epoch FROM peer_deliveries WHERE room_id=?`, room)
	if err != nil {
		return nil, err
	}
	deliveries := make(map[string]delivery)
	for rows.Next() {
		var d delivery
		if err := rows.Scan(&d.id, &d.actor, &d.epoch); err != nil {
			rows.Close()
			return nil, err
		}
		deliveries[domain.PeerDeliveryKey(room, d.id)] = d
	}
	err = rows.Err()
	rows.Close()
	if err != nil {
		return nil, err
	}

	matches, err := s.readDB.QueryContext(ctx, `
		WITH calls AS (
			SELECT e.task_id,e.revision,e.action_json,u.client_message_id,
			       CASE
			         WHEN NOT json_valid(a.detail_json) THEN NULL
			         WHEN a.summary='mcp__ao__peer_task' AND json_type(a.detail_json,'$.input')='object'
			           THEN json_extract(a.detail_json,'$.input')
			         WHEN a.summary='Called ao/peer_task' AND json_type(a.detail_json,'$.arguments')='object'
			           THEN json_extract(a.detail_json,'$.arguments')
			       END AS args
			FROM peer_task_events e
			JOIN conversation_turns t ON t.handled_by_session_id=json_extract(e.action_json,'$.actor')
			  AND t.rolled_back_at IS NULL AND t.promoted_to_turn_id IS NULL
			JOIN conversation_activities a ON a.turn_id=t.id AND a.conversation_id=t.conversation_id
			  AND a.kind='mcp_tool' AND a.status='completed'
			  AND a.summary IN ('mcp__ao__peer_task','Called ao/peer_task')
			JOIN conversation_messages u ON u.turn_id=t.id AND u.conversation_id=t.conversation_id
			  AND u.role='user' AND u.client_message_id LIKE 'peer:%'
			WHERE e.room_id=? AND e.task_id IN (SELECT value FROM json_each(?))
			  AND json_extract(e.action_json,'$.action')='submit'
		)
		SELECT DISTINCT task_id,revision,client_message_id,json_extract(action_json,'$.actor'),json_extract(action_json,'$.epoch')
		FROM calls
		WHERE json_extract(args,'$.action')='submit' AND json_extract(args,'$.id')=task_id
		  AND json_extract(args,'$.revision')=revision-1
		  AND json_extract(args,'$.commit')=json_extract(action_json,'$.commit')
		ORDER BY task_id,revision`, room, string(ids))
	if err != nil {
		return nil, err
	}
	defer matches.Close()

	links := make(map[string]string)
	revisions := make(map[string]int64)
	for matches.Next() {
		var task, key, actor string
		var revision, epoch int64
		if err := matches.Scan(&task, &revision, &key, &actor, &epoch); err != nil {
			return nil, err
		}
		d, ok := deliveries[key]
		if !ok || d.actor != actor || d.epoch != epoch {
			continue
		}
		if earlier, found := revisions[task]; !found {
			revisions[task] = revision
			links[task] = d.id
		} else if revision == earlier && links[task] != d.id {
			// Do not select a winner when the same submit matches multiple turns.
			links[task] = ""
		}
	}
	return links, matches.Err()
}
