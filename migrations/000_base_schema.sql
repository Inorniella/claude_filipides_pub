-- 000: base schema. Idempotent: safe to run several times.
--
-- Single-athlete design: `users` holds one row, which is created below if the table is empty.
-- Every table has RLS enabled and NO policies for anon/authenticated, so the public Supabase
-- REST API cannot read or write anything. Access goes through the `postgres` role
-- (this tool, the Supabase MCP) or through the optional `claude_planner` role (migration 003).

CREATE TABLE IF NOT EXISTS users (
    id         UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    email      TEXT,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

-- The single athlete.
INSERT INTO users (id) SELECT gen_random_uuid() WHERE NOT EXISTS (SELECT 1 FROM users);

-- Encrypted third-party session tokens (Garmin). The key lives in ENCRYPTION_KEY, never here.
CREATE TABLE IF NOT EXISTS user_sessions (
    user_id    UUID NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    source     VARCHAR(20) NOT NULL,
    token_enc  TEXT NOT NULL,
    expires_at TIMESTAMP,
    created_at TIMESTAMP NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMP NOT NULL DEFAULT NOW(),
    PRIMARY KEY (user_id, source)
);

-- No DEFAULT on id: the application generates the UUID.
CREATE TABLE IF NOT EXISTS activities (
    id               UUID PRIMARY KEY,
    user_id          UUID NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    source_id        TEXT NOT NULL,
    source           VARCHAR(20) NOT NULL DEFAULT 'garmin',
    start_time       TIMESTAMP NOT NULL,            -- local time as reported by Garmin
    name             TEXT,
    sport            VARCHAR(40),
    distance_km      DOUBLE PRECISION,
    duration_sec     INTEGER,
    avg_pace_min_km  DOUBLE PRECISION,
    avg_hr           INTEGER,
    max_hr           INTEGER,
    calories         INTEGER,
    elevation_gain_m DOUBLE PRECISION,
    avg_cadence      INTEGER,
    training_load    DOUBLE PRECISION,
    vo2max_estimate  DOUBLE PRECISION,
    avg_temperature  DOUBLE PRECISION,
    avg_power_watts  INTEGER,
    avg_swolf        DOUBLE PRECISION,
    raw_json         JSONB,
    total_splits     INTEGER NOT NULL DEFAULT 0,
    -- Filled in by you (or Claude), not by the sync:
    rpe              INTEGER CHECK (rpe IS NULL OR rpe BETWEEN 1 AND 10),
    feeling          TEXT,
    user_notes       TEXT,
    synced_at        TIMESTAMPTZ,
    UNIQUE (user_id, source_id, source)
);

CREATE INDEX IF NOT EXISTS idx_activities_user_start ON activities (user_id, start_time DESC);

CREATE TABLE IF NOT EXISTS splits (
    id               BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    activity_id      UUID NOT NULL REFERENCES activities(id) ON DELETE CASCADE,
    user_id          UUID NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    km               INTEGER,
    distance_m       DOUBLE PRECISION,
    duration_sec     DOUBLE PRECISION,
    pace_min_km      DOUBLE PRECISION,
    avg_hr           INTEGER,
    max_hr           INTEGER,
    avg_cadence      INTEGER,
    elevation_gain_m DOUBLE PRECISION,
    elevation_loss_m DOUBLE PRECISION,
    temperature      DOUBLE PRECISION,
    power_watts      INTEGER
);

CREATE INDEX IF NOT EXISTS idx_splits_activity ON splits (activity_id);

-- Extended with more columns in 001.
CREATE TABLE IF NOT EXISTS wellness_log (
    id            BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    user_id       UUID NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    date          DATE NOT NULL,
    hrv_ms        DOUBLE PRECISION,
    sleep_hours   DOUBLE PRECISION,
    sleep_quality INTEGER CHECK (sleep_quality IS NULL OR sleep_quality BETWEEN 1 AND 5),  -- manual, 1-5
    rhr_bpm       INTEGER,
    source        VARCHAR(20) NOT NULL DEFAULT 'manual',
    UNIQUE (user_id, date)
);

CREATE TABLE IF NOT EXISTS sync_log (
    id                 BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    user_id            UUID NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    synced_at          TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    source             VARCHAR(20) NOT NULL,
    mode               VARCHAR(20),
    from_date          DATE,
    activities_fetched INTEGER,
    activities_new     INTEGER,
    splits_saved       INTEGER,
    duration_sec       DOUBLE PRECISION,
    status             VARCHAR(10),
    error              TEXT
);

ALTER TABLE users         ENABLE ROW LEVEL SECURITY;
ALTER TABLE user_sessions ENABLE ROW LEVEL SECURITY;
ALTER TABLE activities    ENABLE ROW LEVEL SECURITY;
ALTER TABLE splits        ENABLE ROW LEVEL SECURITY;
ALTER TABLE wellness_log  ENABLE ROW LEVEL SECURITY;
ALTER TABLE sync_log      ENABLE ROW LEVEL SECURITY;
