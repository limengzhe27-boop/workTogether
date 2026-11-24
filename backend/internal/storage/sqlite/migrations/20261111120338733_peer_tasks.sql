-- +goose Up
CREATE TABLE peer_tasks(room_id TEXT NOT NULL REFERENCES peer_rooms(id),id TEXT NOT NULL,goal TEXT NOT NULL,state TEXT NOT NULL CHECK(state IN ('open','claimed','submitted','accepted')),owner TEXT NOT NULL DEFAULT '',revision INTEGER NOT NULL DEFAULT 0,evidence TEXT NOT NULL DEFAULT '',candidate_commit TEXT NOT NULL DEFAULT '',reviewer TEXT NOT NULL DEFAULT '',PRIMARY KEY(room_id,id));
CREATE TABLE peer_task_events(room_id TEXT NOT NULL,task_id TEXT NOT NULL,revision INTEGER NOT NULL,action_json TEXT NOT NULL,PRIMARY KEY(room_id,task_id,revision),FOREIGN KEY(room_id,task_id) REFERENCES peer_tasks(room_id,id));
-- +goose Down
DROP TABLE peer_task_events;
DROP TABLE peer_tasks;
