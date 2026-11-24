-- +goose Up
ALTER TABLE peer_rooms ADD COLUMN member_settings TEXT NOT NULL DEFAULT '{}';
ALTER TABLE peer_deliveries ADD COLUMN round_id TEXT NOT NULL DEFAULT '';
CREATE INDEX peer_round_sender ON peer_deliveries(room_id,round_id,sender);

-- +goose Down
DROP INDEX peer_round_sender;
ALTER TABLE peer_deliveries DROP COLUMN round_id;
ALTER TABLE peer_rooms DROP COLUMN member_settings;
