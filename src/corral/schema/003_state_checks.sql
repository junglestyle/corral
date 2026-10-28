-- jobs.state and jobs.stop get CHECKs listing the values of JobState and Stop. SQLite can't add a CHECK to a column,
-- so the table is rebuilt.
CREATE TABLE jobs_new (
    id INTEGER PRIMARY KEY,
    kind TEXT NOT NULL,
    needs TEXT NOT NULL,
    priority INTEGER NOT NULL,
    args TEXT NOT NULL,
    key TEXT,
    then_specs TEXT NOT NULL,
    max_attempts INTEGER NOT NULL,
    state TEXT NOT NULL CHECK (state IN ('queued', 'leased', 'succeeded', 'failed', 'cancelled')),
    attempts INTEGER NOT NULL DEFAULT 0,
    enqueued_at REAL NOT NULL,
    worker TEXT,
    lease_id TEXT UNIQUE,
    lease_expires REAL,
    stop TEXT CHECK (stop IN ('requeue', 'cancel')),
    started_at REAL,
    finished_at REAL,
    exit_code INTEGER,
    result TEXT
);
INSERT INTO jobs_new
    (id, kind, needs, priority, args, key, then_specs, max_attempts, state, attempts, enqueued_at, worker, lease_id,
     lease_expires, stop, started_at, finished_at, exit_code, result)
SELECT
    id, kind, needs, priority, args, key, then_specs, max_attempts, state, attempts, enqueued_at, worker, lease_id,
    lease_expires, stop, started_at, finished_at, exit_code, result
FROM jobs;
DROP TABLE jobs;
ALTER TABLE jobs_new RENAME TO jobs;
CREATE INDEX jobs_state ON jobs (state);
-- Not partial on state = 'queued' like the old one: queries bind the state, and SQLite can't match a bound
-- parameter against a partial index's WHERE.
CREATE INDEX jobs_key ON jobs (key);
