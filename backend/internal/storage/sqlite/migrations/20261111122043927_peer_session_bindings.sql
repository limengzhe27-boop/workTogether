-- +goose Up
CREATE TABLE peer_session_bindings(session_id TEXT PRIMARY KEY);
INSERT OR IGNORE INTO peer_session_bindings SELECT j.value FROM peer_rooms r,json_each(r.members) j;
-- +goose Down
DROP TABLE peer_session_bindings;
