-- =============================================================
-- OWNER: AARON
-- PostgreSQL Schema for the Production Pipeline
-- Run this once against hyperspectral_db
-- =============================================================

-- Users table (for authentication)
CREATE TABLE IF NOT EXISTS users (
    id            SERIAL PRIMARY KEY,
    email         VARCHAR(255) UNIQUE NOT NULL,
    password_hash VARCHAR(255) NOT NULL,
    name          VARCHAR(255),
    created_at    TIMESTAMPTZ DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_users_email ON users(email);

-- Scenes table (audit log for every processed scene)
CREATE TABLE IF NOT EXISTS scenes (
    id                  SERIAL PRIMARY KEY,
    scene_id            VARCHAR(64) UNIQUE NOT NULL,
    satellite           VARCHAR(32) NOT NULL DEFAULT 'EMIT',
    bbox                TEXT,
    status              VARCHAR(32) NOT NULL DEFAULT 'STARTING',
    processing_time_sec FLOAT,
    created_at          TIMESTAMPTZ DEFAULT NOW(),
    completed_at        TIMESTAMPTZ
);

CREATE INDEX IF NOT EXISTS idx_scenes_scene_id ON scenes(scene_id);
CREATE INDEX IF NOT EXISTS idx_scenes_status   ON scenes(status);
CREATE INDEX IF NOT EXISTS idx_scenes_created  ON scenes(created_at DESC);
