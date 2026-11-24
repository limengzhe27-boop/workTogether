-- +goose Up
CREATE TABLE peer_tool_grants(session_id TEXT PRIMARY KEY REFERENCES sessions(id),room_id TEXT NOT NULL REFERENCES peer_rooms(id),epoch INTEGER NOT NULL);
-- +goose Down
DROP TABLE peer_tool_grants;
