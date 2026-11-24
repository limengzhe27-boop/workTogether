package store

import (
	"context"
	"encoding/json"
	"net/url"
	"path"
	"strings"

	"github.com/aoagents/agent-orchestrator/backend/internal/domain"
)

func (s *Store) loadPeerGeneratedImages(ctx context.Context, room string, deliveries []domain.PeerDelivery) error {
	if len(deliveries) == 0 {
		return nil
	}
	keys := make(map[string]int, len(deliveries))
	inputs := make([]string, len(deliveries))
	for i, delivery := range deliveries {
		key := domain.PeerDeliveryKey(room, delivery.ID)
		keys[key] = i
		inputs[i] = key
	}
	encoded, err := json.Marshal(inputs)
	if err != nil {
		return err
	}
	rows, err := s.readDB.QueryContext(ctx, `
		SELECT DISTINCT u.client_message_id,t.handled_by_session_id,a.id,
		  CASE WHEN a.status='running' AND t.state IN ('interrupted','cancelled') THEN 'cancelled'
		       WHEN a.status='running' AND t.state NOT IN ('queued','running') THEN 'recovered'
		       ELSE a.status END,
		  a.detail_json,a.sequence
		FROM json_each(?) k
		JOIN conversation_messages u ON u.client_message_id=k.value AND u.role='user'
		JOIN conversation_turns t ON t.id=u.turn_id AND t.conversation_id=u.conversation_id
		  AND t.rolled_back_at IS NULL AND t.promoted_to_turn_id IS NULL
		JOIN conversation_activities a ON a.turn_id=t.id AND a.conversation_id=t.conversation_id
		  AND a.kind='system' AND a.status IN ('running','completed','failed','cancelled','recovered')
		WHERE json_extract((CASE WHEN json_valid(a.detail_json) THEN a.detail_json ELSE '{}' END),'$.event')='image.generated'
		ORDER BY a.sequence`, string(encoded))
	if err != nil {
		return err
	}
	defer rows.Close()
	for rows.Next() {
		var key, actor, detailJSON string
		var image domain.PeerGeneratedImage
		var sequence int64
		if err := rows.Scan(&key, &actor, &image.ID, &image.Status, &detailJSON, &sequence); err != nil {
			return err
		}
		i, ok := keys[key]
		if !ok || actor != deliveries[i].Target {
			continue
		}
		var detail struct {
			Image struct {
				Name string `json:"name"`
				URL  string `json:"url"`
			} `json:"image"`
		}
		if json.Unmarshal([]byte(detailJSON), &detail) != nil {
			continue
		}
		if detail.Image.URL != "" {
			prefix := "/api/v1/sessions/" + url.PathEscape(actor) + "/artifact-files/"
			name := strings.TrimPrefix(detail.Image.URL, prefix)
			if name == detail.Image.URL || path.Base(name) != name || !strings.HasPrefix(name, "generated-image-") {
				continue
			}
			image.Name, image.URL = detail.Image.Name, detail.Image.URL
		}
		if len(deliveries[i].GeneratedImages) < 20 {
			deliveries[i].GeneratedImages = append(deliveries[i].GeneratedImages, image)
		}
	}
	return rows.Err()
}
