-- 003 (OPTIONAL): least-privilege role for a generic Postgres/MCP client.
-- Reads the `coach` views and the base tables; only writes to planned_workouts.
-- It has no LOGIN and no password: set one yourself, outside the repository:
--     ALTER ROLE claude_planner LOGIN PASSWORD '...';
-- Not needed if you use the official Supabase MCP, which connects with project-level permissions.

DO $$
BEGIN
    IF NOT EXISTS (SELECT FROM pg_roles WHERE rolname = 'claude_planner') THEN
        CREATE ROLE claude_planner NOLOGIN;
    END IF;
END $$;

GRANT USAGE ON SCHEMA coach TO claude_planner;
GRANT SELECT ON ALL TABLES IN SCHEMA coach TO claude_planner;
GRANT EXECUTE ON FUNCTION coach.sport_family(TEXT), coach.athlete_id() TO claude_planner;

GRANT SELECT ON public.activities, public.splits, public.wellness_log TO claude_planner;
GRANT SELECT, INSERT, UPDATE, DELETE ON public.planned_workouts TO claude_planner;

-- The tables have RLS: without a policy, the role would see no rows.
DO $$
DECLARE t TEXT;
BEGIN
    FOREACH t IN ARRAY ARRAY['activities', 'splits', 'wellness_log'] LOOP
        EXECUTE format('DROP POLICY IF EXISTS claude_planner_read ON public.%I', t);
        EXECUTE format('CREATE POLICY claude_planner_read ON public.%I FOR SELECT TO claude_planner USING (true)', t);
    END LOOP;
END $$;

DROP POLICY IF EXISTS claude_planner_all ON public.planned_workouts;
CREATE POLICY claude_planner_all ON public.planned_workouts
    FOR ALL TO claude_planner USING (true) WITH CHECK (true);
