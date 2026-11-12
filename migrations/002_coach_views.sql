-- 002: `coach` schema with pre-summarized views so Claude does not read raw data.
-- The `coach` schema is NOT exposed by the Supabase REST API (it only exposes `public`),
-- so these views are not reachable with the anon key.
-- If you add `coach` to "Exposed schemas" in Supabase, revoke the default grants first.

CREATE SCHEMA IF NOT EXISTS coach;

-- Sport family of a Garmin activity, aligned with planned_workouts.sport.
CREATE OR REPLACE FUNCTION coach.sport_family(sport TEXT) RETURNS TEXT
LANGUAGE sql IMMUTABLE AS $$
    SELECT CASE
        WHEN sport IN ('running', 'trail_running', 'treadmill_running') THEN 'running'
        WHEN sport IN ('cycling', 'road_biking', 'indoor_cycling', 'virtual_ride',
                       'gravel_cycling', 'mountain_biking') THEN 'cycling'
        WHEN sport IN ('lap_swimming', 'open_water_swimming', 'swimming') THEN 'swimming'
        ELSE 'other'
    END
$$;

-- Single athlete: allows inserting into planned_workouts without knowing the users UUID.
CREATE OR REPLACE FUNCTION coach.athlete_id() RETURNS UUID
LANGUAGE sql STABLE SECURITY DEFINER SET search_path = public, pg_temp AS $$
    SELECT id FROM users ORDER BY created_at LIMIT 1
$$;
REVOKE ALL ON FUNCTION coach.athlete_id() FROM PUBLIC;

ALTER TABLE planned_workouts ALTER COLUMN user_id SET DEFAULT coach.athlete_id();

-- ── Recent activities ─────────────────────────────────────────────────────
CREATE OR REPLACE VIEW coach.recent_activities AS
SELECT user_id,
       start_time,
       coach.sport_family(sport)              AS sport_family,
       sport,
       name,
       distance_km,
       round(duration_sec / 60.0, 1)          AS minutes,
       avg_pace_min_km,
       avg_hr,
       max_hr,
       elevation_gain_m,
       training_load,
       rpe,
       feeling,
       user_notes
FROM public.activities
WHERE start_time >= now() - interval '120 days'
ORDER BY start_time DESC;

COMMENT ON VIEW coach.recent_activities IS 'Activities from the last 120 days, without splits or raw JSON.';

-- ── Weekly load ───────────────────────────────────────────────────────────
-- load_approx uses Garmin's training_load and, if missing, the activity minutes.
CREATE OR REPLACE VIEW coach.weekly_load AS
SELECT user_id,
       date_trunc('week', start_time)::date                              AS week_start,
       count(*)                                                          AS sessions,
       round(sum(distance_km)::numeric, 1)                               AS km,
       round((sum(duration_sec) / 3600.0)::numeric, 2)                   AS hours,
       round(coalesce(sum(elevation_gain_m), 0)::numeric)                AS elevation_m,
       round(sum(coalesce(training_load, duration_sec / 60.0))::numeric) AS load_approx
FROM public.activities
GROUP BY user_id, date_trunc('week', start_time)
ORDER BY week_start DESC;

COMMENT ON VIEW coach.weekly_load IS 'Volume and load per week (Monday). load_approx = Garmin training_load or minutes.';

-- ── Daily ACWR (approximate) ────────────────────────────────────────────────
-- acute = 7-day load; chronic = weekly average over 28 days. Rough sweet spot 0.8-1.3.
CREATE OR REPLACE VIEW coach.acwr_daily AS
WITH days AS (
    SELECT generate_series(current_date - 119, current_date, interval '1 day')::date AS day
), per_day AS (
    SELECT user_id, start_time::date AS day,
           sum(coalesce(training_load, duration_sec / 60.0)) AS daily_load
    FROM public.activities
    GROUP BY user_id, start_time::date
), grid AS (
    SELECT u.user_id, d.day, coalesce(p.daily_load, 0) AS daily_load
    FROM (SELECT DISTINCT user_id FROM public.activities) u
    CROSS JOIN days d
    LEFT JOIN per_day p ON p.user_id = u.user_id AND p.day = d.day
), rolled AS (
    SELECT user_id, day, daily_load,
           sum(daily_load) OVER w7                         AS acute_7d,
           sum(daily_load) OVER w28 / 4.0                  AS chronic_weekly_avg
    FROM grid
    WINDOW w7  AS (PARTITION BY user_id ORDER BY day ROWS BETWEEN 6  PRECEDING AND CURRENT ROW),
           w28 AS (PARTITION BY user_id ORDER BY day ROWS BETWEEN 27 PRECEDING AND CURRENT ROW)
)
SELECT user_id, day,
       round(daily_load::numeric)         AS daily_load,
       round(acute_7d::numeric)           AS acute_7d,
       round(chronic_weekly_avg::numeric) AS chronic_weekly_avg,
       CASE WHEN chronic_weekly_avg > 0 THEN round((acute_7d / chronic_weekly_avg)::numeric, 2) END AS acwr
FROM rolled
WHERE day >= current_date - 89
ORDER BY day DESC;

COMMENT ON VIEW coach.acwr_daily IS 'Approximate ACWR per day (90 days). The first 28 days of history give unreliable values.';

-- ── Wellness trend ──────────────────────────────────────────────────
CREATE OR REPLACE VIEW coach.wellness_trend AS
SELECT user_id,
       date,
       hrv_ms,
       round(avg(hrv_ms) OVER w7::numeric, 1)  AS hrv_7d_avg,
       round(avg(hrv_ms) OVER w60::numeric, 1) AS hrv_60d_avg,
       rhr_bpm,
       round(avg(rhr_bpm) OVER w7::numeric, 1)  AS rhr_7d_avg,
       round(avg(rhr_bpm) OVER w60::numeric, 1) AS rhr_60d_avg,
       sleep_hours,
       sleep_score,
       body_battery_max,
       stress_avg,
       training_readiness
FROM public.wellness_log
WINDOW w7  AS (PARTITION BY user_id ORDER BY date ROWS BETWEEN 6  PRECEDING AND CURRENT ROW),
       w60 AS (PARTITION BY user_id ORDER BY date ROWS BETWEEN 59 PRECEDING AND CURRENT ROW)
ORDER BY date DESC;

COMMENT ON VIEW coach.wellness_trend IS 'Daily wellness with 7- and 60-day averages (over rows with data, not calendar days).';

-- ── Plan vs. actual ──────────────────────────────────────────────────
CREATE OR REPLACE VIEW coach.plan_vs_actual AS
SELECT p.id,
       p.user_id,
       p.planned_date,
       p.sport,
       p.title,
       p.session_type,
       p.status,
       p.target_distance_km,
       p.target_duration_min,
       p.target_rpe,
       a.distance_km                       AS actual_distance_km,
       round(a.duration_sec / 60.0, 1)     AS actual_minutes,
       a.avg_hr                            AS actual_avg_hr,
       a.rpe                               AS actual_rpe,
       a.id IS NOT NULL                    AS has_activity
FROM public.planned_workouts p
LEFT JOIN LATERAL (
    SELECT x.* FROM public.activities x
    WHERE x.user_id = p.user_id
      AND x.start_time::date = p.planned_date
      AND coach.sport_family(x.sport) = p.sport
    ORDER BY x.duration_sec DESC
    LIMIT 1
) a ON true
ORDER BY p.planned_date DESC;

COMMENT ON VIEW coach.plan_vs_actual IS 'Each planned workout with the longest activity of that day and sport, if any.';
