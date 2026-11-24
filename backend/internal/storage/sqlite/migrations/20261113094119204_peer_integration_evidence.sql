-- +goose Up
CREATE TABLE peer_integration_candidates (
 room_id TEXT NOT NULL REFERENCES peer_rooms(id),
 candidate_commit TEXT NOT NULL,
 branch TEXT NOT NULL,
 tasks_json TEXT NOT NULL,
 created_at TEXT NOT NULL,
 PRIMARY KEY(room_id,candidate_commit)
);
CREATE TABLE peer_integration_checks (
 room_id TEXT NOT NULL,
 id TEXT NOT NULL,
 candidate_commit TEXT NOT NULL,
 executor TEXT NOT NULL,
 command TEXT NOT NULL,
 cwd TEXT NOT NULL,
 exit_code INTEGER NOT NULL,
 scope TEXT NOT NULL,
 created_at TEXT NOT NULL,
 PRIMARY KEY(room_id,id),
 FOREIGN KEY(room_id,candidate_commit) REFERENCES peer_integration_candidates(room_id,candidate_commit)
);
CREATE TABLE peer_execution_controls (
 room_id TEXT NOT NULL,
 delivery_id TEXT NOT NULL,
 id TEXT NOT NULL,
 digest TEXT NOT NULL,
 status TEXT NOT NULL,
 PRIMARY KEY(room_id,delivery_id,id),
 FOREIGN KEY(room_id,delivery_id) REFERENCES peer_deliveries(room_id,id)
);
-- +goose Down
DROP TABLE peer_execution_controls;
DROP TABLE peer_integration_checks;
DROP TABLE peer_integration_candidates;
