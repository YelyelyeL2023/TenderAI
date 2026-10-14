-- BIN/IINs the Subjects API returned nothing for; re-checked weekly instead of on every incremental run.
CREATE TABLE IF NOT EXISTS ops.subject_miss (
    biin       text PRIMARY KEY,
    checked_at timestamptz NOT NULL DEFAULT now()
);
