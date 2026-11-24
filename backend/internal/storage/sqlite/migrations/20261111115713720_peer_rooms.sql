-- +goose Up
CREATE TABLE peer_rooms (id TEXT PRIMARY KEY, project_id TEXT NOT NULL REFERENCES projects(id), members TEXT NOT NULL, epoch INTEGER NOT NULL, budget INTEGER NOT NULL CHECK(budget BETWEEN 1 AND 100), used INTEGER NOT NULL DEFAULT 0, paused INTEGER NOT NULL DEFAULT 0);
CREATE TABLE peer_deliveries (room_id TEXT NOT NULL REFERENCES peer_rooms(id), id TEXT NOT NULL, digest TEXT NOT NULL, state TEXT NOT NULL CHECK(state IN ('pending','accepted','uncertain')), PRIMARY KEY(room_id,id));
-- +goose Down
DROP TABLE peer_deliveries;
DROP TABLE peer_rooms;
