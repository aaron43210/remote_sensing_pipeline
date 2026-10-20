-- HydraNet platform schema (PostgreSQL 16 + PostGIS). Applied by pkg/db.Migrate.
CREATE EXTENSION IF NOT EXISTS postgis;
CREATE EXTENSION IF NOT EXISTS pgcrypto;

CREATE TABLE IF NOT EXISTS users (
    id            BIGSERIAL PRIMARY KEY,
    email         TEXT UNIQUE NOT NULL,
    password_hash TEXT NOT NULL,
    name          TEXT NOT NULL,
    role          TEXT NOT NULL DEFAULT 'user',          -- user | admin
    created_at    TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS jobs (
    job_id          TEXT PRIMARY KEY,
    user_id         BIGINT NOT NULL REFERENCES users(id),
    bbox            GEOMETRY(Polygon, 4326) NOT NULL,
    bbox_arr        DOUBLE PRECISION[] NOT NULL,          -- lon_min, lat_min, lon_max, lat_max
    tasks           TEXT[] NOT NULL,
    date_from       DATE,
    date_to         DATE,
    source          TEXT NOT NULL DEFAULT 'nasa',         -- nasa | fixture
    status          TEXT NOT NULL DEFAULT 'QUEUED',       -- QUEUED RUNNING COMPLETED PARTIAL FAILED NO_DATA CANCELLED
    error           TEXT,
    model_version   TEXT,
    reuse_key       TEXT,
    reused_from     TEXT,
    acquired_emit   TIMESTAMPTZ,
    acquired_landsat TIMESTAMPTZ,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
    completed_at    TIMESTAMPTZ
);
CREATE INDEX IF NOT EXISTS jobs_user_created ON jobs (user_id, created_at DESC);
CREATE INDEX IF NOT EXISTS jobs_status ON jobs (status) WHERE status IN ('QUEUED', 'RUNNING');
CREATE INDEX IF NOT EXISTS jobs_reuse ON jobs (reuse_key) WHERE status = 'COMPLETED';
CREATE INDEX IF NOT EXISTS jobs_bbox ON jobs USING GIST (bbox);

CREATE TABLE IF NOT EXISTS job_tasks (
    job_id      TEXT NOT NULL REFERENCES jobs(job_id) ON DELETE CASCADE,
    task        TEXT NOT NULL,                         -- agriculture | mineral | thermal
    stage       TEXT NOT NULL DEFAULT 'queued',        -- queued ingest preprocess inference done
    state       TEXT NOT NULL DEFAULT 'PENDING',       -- PENDING RUNNING DONE ERROR NO_DATA TIMED_OUT CANCELLED
    message     TEXT NOT NULL DEFAULT '',
    started_at  TIMESTAMPTZ,
    finished_at TIMESTAMPTZ,
    updated_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (job_id, task)
);

CREATE TABLE IF NOT EXISTS job_sources (
    job_id      TEXT NOT NULL REFERENCES jobs(job_id) ON DELETE CASCADE,
    sensor      TEXT NOT NULL,
    granule_id  TEXT NOT NULL,
    acquired_at TIMESTAMPTZ,
    PRIMARY KEY (job_id, sensor)
);

CREATE TABLE IF NOT EXISTS layers (
    job_id   TEXT NOT NULL REFERENCES jobs(job_id) ON DELETE CASCADE,
    task     TEXT NOT NULL,
    name     TEXT NOT NULL,
    tif_key  TEXT NOT NULL,
    png_key  TEXT,
    bounds   DOUBLE PRECISION[] NOT NULL,
    units    TEXT NOT NULL DEFAULT '',
    kind     TEXT NOT NULL DEFAULT 'continuous',
    colormap TEXT NOT NULL DEFAULT 'viridis',
    vmin     DOUBLE PRECISION,
    vmax     DOUBLE PRECISION,
    classes  TEXT[],
    stats    JSONB NOT NULL DEFAULT '{}',
    PRIMARY KEY (job_id, task, name)
);

CREATE TABLE IF NOT EXISTS job_results (
    job_id        TEXT NOT NULL REFERENCES jobs(job_id) ON DELETE CASCADE,
    task          TEXT NOT NULL,
    model_version TEXT NOT NULL,
    summary       JSONB NOT NULL DEFAULT '{}',
    verification  JSONB NOT NULL DEFAULT '{}',
    timings       JSONB NOT NULL DEFAULT '{}',
    created_at    TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (job_id, task)
);

CREATE TABLE IF NOT EXISTS catalog_granules (
    granule_id  TEXT PRIMARY KEY,
    sensor      TEXT NOT NULL,                         -- EMIT | LANDSAT
    acquired_at TIMESTAMPTZ NOT NULL,
    cloud_pct   DOUBLE PRECISION,
    footprint   GEOMETRY(MultiPolygon, 4326) NOT NULL,
    url         TEXT,
    bronze_key  TEXT,
    status      TEXT NOT NULL DEFAULT 'indexed',       -- indexed | staged
    updated_at  TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS catalog_footprint ON catalog_granules USING GIST (footprint);
CREATE INDEX IF NOT EXISTS catalog_sensor_time ON catalog_granules (sensor, acquired_at DESC);

CREATE TABLE IF NOT EXISTS model_versions (
    version           TEXT PRIMARY KEY,
    card              JSONB NOT NULL,
    map_verification  JSONB NOT NULL DEFAULT '{}',
    s3_key            TEXT NOT NULL,
    feature_spec_hash TEXT NOT NULL,
    active            BOOLEAN NOT NULL DEFAULT false,
    promoted_at       TIMESTAMPTZ,
    created_at        TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE UNIQUE INDEX IF NOT EXISTS one_active_model ON model_versions (active) WHERE active;

CREATE TABLE IF NOT EXISTS outbox (
    id         BIGSERIAL PRIMARY KEY,
    topic      TEXT NOT NULL,
    key        TEXT NOT NULL,
    payload    JSONB NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    sent_at    TIMESTAMPTZ,
    attempts   INT NOT NULL DEFAULT 0
);
CREATE INDEX IF NOT EXISTS outbox_unsent ON outbox (id) WHERE sent_at IS NULL;

CREATE TABLE IF NOT EXISTS processed_events (
    event_id     TEXT NOT NULL,
    consumer     TEXT NOT NULL,
    processed_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (event_id, consumer)
);

CREATE TABLE IF NOT EXISTS dq_results (
    id         BIGSERIAL PRIMARY KEY,
    run_id     TEXT NOT NULL,
    dataset    TEXT NOT NULL,
    check_name TEXT NOT NULL,
    passed     BOOLEAN NOT NULL,
    observed   JSONB NOT NULL DEFAULT '{}',
    ts         TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- wake the outbox relay immediately instead of waiting for its poll
CREATE OR REPLACE FUNCTION notify_outbox() RETURNS trigger AS $$
BEGIN PERFORM pg_notify('outbox', NEW.id::text); RETURN NEW; END; $$ LANGUAGE plpgsql;
DROP TRIGGER IF EXISTS outbox_notify ON outbox;
CREATE TRIGGER outbox_notify AFTER INSERT ON outbox FOR EACH ROW EXECUTE FUNCTION notify_outbox();
