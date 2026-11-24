-- +goose Up
ALTER TABLE peer_deliveries ADD COLUMN attachments TEXT NOT NULL DEFAULT '[]';

-- +goose Down
ALTER TABLE peer_deliveries DROP COLUMN attachments;
