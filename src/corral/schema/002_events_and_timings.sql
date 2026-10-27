ALTER TABLE workers ADD COLUMN blocked_since REAL;
ALTER TABLE workers ADD COLUMN block_alerted INTEGER NOT NULL DEFAULT 0 CHECK (block_alerted IN (0, 1));

-- The outbox: an event lives until every webhook it was queued for has taken it or been given up on.
CREATE TABLE events (
    id INTEGER PRIMARY KEY,
    body TEXT NOT NULL,
    created_at REAL NOT NULL
);
CREATE TABLE deliveries (
    event_id INTEGER NOT NULL,
    url TEXT NOT NULL,
    attempts INTEGER NOT NULL DEFAULT 0,
    next_at REAL NOT NULL,
    PRIMARY KEY (event_id, url)
);
CREATE INDEX deliveries_next_at ON deliveries (next_at);

CREATE TABLE kind_timings (
    kind TEXT PRIMARY KEY,
    seconds_per_item REAL NOT NULL
);
