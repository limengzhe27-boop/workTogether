-- +goose Up
ALTER TABLE peer_deliveries ADD COLUMN broadcast_id TEXT NOT NULL DEFAULT '';

-- +goose Down
ALTER TABLE peer_deliveries DROP COLUMN broadcast_id;
