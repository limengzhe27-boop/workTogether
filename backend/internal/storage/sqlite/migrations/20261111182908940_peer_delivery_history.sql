-- +goose Up
ALTER TABLE peer_deliveries ADD COLUMN sender TEXT NOT NULL DEFAULT '';
ALTER TABLE peer_deliveries ADD COLUMN target TEXT NOT NULL DEFAULT '';
ALTER TABLE peer_deliveries ADD COLUMN message TEXT NOT NULL DEFAULT '';
ALTER TABLE peer_deliveries ADD COLUMN epoch INTEGER NOT NULL DEFAULT 0;
ALTER TABLE peer_deliveries ADD COLUMN created_at TEXT NOT NULL DEFAULT '';
-- +goose Down
ALTER TABLE peer_deliveries DROP COLUMN created_at;
ALTER TABLE peer_deliveries DROP COLUMN epoch;
ALTER TABLE peer_deliveries DROP COLUMN message;
ALTER TABLE peer_deliveries DROP COLUMN target;
ALTER TABLE peer_deliveries DROP COLUMN sender;
