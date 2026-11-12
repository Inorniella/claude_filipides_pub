-- 001: extends wellness_log (created in 000) and creates planned_workouts.
-- Idempotent: safe to run several times.

-- ── Daily wellness ────────────────────────────────────────────────────────
-- sleep_quality (1-5, manual) is kept; the Garmin score (0-100) goes in sleep_score.
ALTER TABLE wellness_log
    ADD COLUMN IF NOT EXISTS sleep_score        INTEGER CHECK (sleep_score IS NULL OR sleep_score BETWEEN 0 AND 100),
    ADD COLUMN IF NOT EXISTS body_battery_max   INTEGER CHECK (body_battery_max IS NULL OR body_battery_max BETWEEN 0 AND 100),
    ADD COLUMN IF NOT EXISTS body_battery_min   INTEGER CHECK (body_battery_min IS NULL OR body_battery_min BETWEEN 0 AND 100),
    ADD COLUMN IF NOT EXISTS stress_avg         INTEGER CHECK (stress_avg IS NULL OR stress_avg BETWEEN 0 AND 100),
    ADD COLUMN IF NOT EXISTS training_readiness INTEGER CHECK (training_readiness IS NULL OR training_readiness BETWEEN 0 AND 100),
    ADD COLUMN IF NOT EXISTS raw                JSONB,
    ADD COLUMN IF NOT EXISTS updated_at         TIMESTAMPTZ DEFAULT NOW();

COMMENT ON TABLE  wellness_log IS 'Daily wellness (one row per day). Source: garmin or manual.';
COMMENT ON COLUMN wellness_log.hrv_ms IS 'Average overnight HRV, in ms.';
COMMENT ON COLUMN wellness_log.sleep_score IS 'Garmin sleep score, 0-100.';
COMMENT ON COLUMN wellness_log.raw IS 'Raw Garmin responses for the day, to reprocess without downloading again.';

-- ── Training plan ───────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS planned_workouts (
    id                  UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id             UUID NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    planned_date        DATE NOT NULL,
    sport               VARCHAR(20) NOT NULL DEFAULT 'running'
                            CHECK (sport IN ('running', 'cycling', 'swimming', 'strength', 'other')),
    title               TEXT NOT NULL,
    session_type        VARCHAR(20)
                            CHECK (session_type IS NULL OR session_type IN
                                   ('easy', 'long', 'tempo', 'intervals', 'hills', 'trail', 'race', 'recovery', 'rest', 'strength', 'other')),
    target_distance_km  DOUBLE PRECISION CHECK (target_distance_km IS NULL OR target_distance_km >= 0),
    target_duration_min INTEGER          CHECK (target_duration_min IS NULL OR target_duration_min >= 0),
    target_elevation_m  DOUBLE PRECISION CHECK (target_elevation_m IS NULL OR target_elevation_m >= 0),
    target_rpe          INTEGER          CHECK (target_rpe IS NULL OR target_rpe BETWEEN 1 AND 10),
    description         TEXT,
    status              VARCHAR(10) NOT NULL DEFAULT 'planned'
                            CHECK (status IN ('planned', 'done', 'skipped', 'modified')),
    created_by          VARCHAR(20) NOT NULL DEFAULT 'claude',
    created_at          TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at          TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_planned_user_date ON planned_workouts (user_id, planned_date);

COMMENT ON TABLE planned_workouts IS
    'Planned workouts (written by Claude or by you). Compared with activities in coach.plan_vs_actual.';
COMMENT ON COLUMN planned_workouts.sport IS 'Sport family: running (includes trail and treadmill), cycling, swimming, strength, other.';
COMMENT ON COLUMN planned_workouts.status IS 'planned | done | skipped | modified';

-- RLS enabled with no policies for anon/authenticated: the public API sees nothing.
-- Access is for the postgres role (ingestion, Supabase MCP) and, if you create it, claude_planner (migration 003).
ALTER TABLE planned_workouts ENABLE ROW LEVEL SECURITY;
