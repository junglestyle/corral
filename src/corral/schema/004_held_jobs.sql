-- Held jobs: one with wait_seconds isn't leased while a queued or running job would enqueue its key, or within
-- quiet_seconds of its last enqueue, for at most wait_seconds from when it was created.
ALTER TABLE jobs ADD COLUMN wait_seconds REAL CHECK (wait_seconds > 0);
ALTER TABLE jobs ADD COLUMN quiet_seconds REAL NOT NULL DEFAULT 0 CHECK (quiet_seconds >= 0);
ALTER TABLE jobs ADD COLUMN last_enqueued_at REAL NOT NULL DEFAULT 0;
UPDATE jobs SET last_enqueued_at = enqueued_at;
