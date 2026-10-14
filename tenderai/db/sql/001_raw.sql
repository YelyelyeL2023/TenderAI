-- Raw layer: exact API payloads, idempotent upserts keyed by (entity, id).
CREATE SCHEMA IF NOT EXISTS raw;
CREATE SCHEMA IF NOT EXISTS core;
CREATE SCHEMA IF NOT EXISTS mart;
CREATE SCHEMA IF NOT EXISTS ops;

CREATE TABLE IF NOT EXISTS raw.ows_object (
    entity        text        NOT NULL,          -- plan_point | trd_buy | lot | contract | subject
    id            bigint      NOT NULL,
    payload       jsonb       NOT NULL,
    payload_hash  text        NOT NULL,          -- md5 of canonical JSON; unchanged hash => no-op
    source        text        NOT NULL,          -- e.g. 'graphql:Plans' / 'journal'
    first_seen_at timestamptz NOT NULL DEFAULT now(),
    fetched_at    timestamptz NOT NULL DEFAULT now(),
    is_deleted    boolean     NOT NULL DEFAULT false,
    PRIMARY KEY (entity, id)
);
CREATE INDEX IF NOT EXISTS ows_object_fetched_idx ON raw.ows_object (entity, fetched_at);

CREATE TABLE IF NOT EXISTS raw.ref (
    ref_name   text        NOT NULL,
    code       text        NOT NULL,             -- id or code, depending on the dictionary
    payload    jsonb       NOT NULL,
    fetched_at timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (ref_name, code)
);

-- Ingestion bookkeeping
CREATE TABLE IF NOT EXISTS ops.ingest_run (
    run_id      bigserial   PRIMARY KEY,
    kind        text        NOT NULL,            -- backfill | incremental | refs
    started_at  timestamptz NOT NULL DEFAULT now(),
    finished_at timestamptz,
    status      text        NOT NULL DEFAULT 'running',
    stats       jsonb       NOT NULL DEFAULT '{}'::jsonb,
    error       text
);

CREATE TABLE IF NOT EXISTS ops.watermark (
    name       text PRIMARY KEY,                 -- e.g. 'journal'
    value      timestamptz NOT NULL,
    updated_at timestamptz NOT NULL DEFAULT now()
);

-- Per (entity, bin) reconciliation: API totalCount vs rows stored
CREATE TABLE IF NOT EXISTS ops.ingest_count (
    run_id     bigint NOT NULL REFERENCES ops.ingest_run(run_id),
    entity     text   NOT NULL,
    scope      text   NOT NULL,                  -- BIN or 'ids'
    api_total  integer,
    fetched    integer NOT NULL,
    PRIMARY KEY (run_id, entity, scope)
);
