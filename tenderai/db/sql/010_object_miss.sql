-- Objects referenced by stored data that the API no longer returns (e.g. superseded plan point versions);
-- re-checked weekly instead of on every incremental run.
CREATE TABLE IF NOT EXISTS ops.object_miss (
    entity     text   NOT NULL,
    id         bigint NOT NULL,
    checked_at timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (entity, id)
);
