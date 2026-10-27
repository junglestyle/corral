-- IF NOT EXISTS because databases made before numbered migrations already have these tables.
CREATE TABLE IF NOT EXISTS jobs (
    id INTEGER PRIMARY KEY,
    kind TEXT NOT NULL,
    needs TEXT NOT NULL,
    priority INTEGER NOT NULL,
    args TEXT NOT NULL,
    key TEXT,
    then_specs TEXT NOT NULL,
    max_attempts INTEGER NOT NULL,
    state TEXT NOT NULL,
    attempts INTEGER NOT NULL DEFAULT 0,
    enqueued_at REAL NOT NULL,
    worker TEXT,
    lease_id TEXT UNIQUE,
    lease_expires REAL,
    stop TEXT,
    started_at REAL,
    finished_at REAL,
    exit_code INTEGER,
    result TEXT
);
CREATE INDEX IF NOT EXISTS jobs_state ON jobs (state);
CREATE INDEX IF NOT EXISTS jobs_key ON jobs (key) WHERE state = 'queued';
CREATE TABLE IF NOT EXISTS workers (
    name TEXT PRIMARY KEY,
    offers TEXT NOT NULL,
    kinds TEXT NOT NULL,
    foreign_procs TEXT NOT NULL,
    last_seen REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS pauses (
    name TEXT PRIMARY KEY,
    until REAL
);
