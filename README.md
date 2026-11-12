# claude-filipides

Download your Garmin data into Supabase and let Claude (a training project on claude.ai, or Claude Code) read it and write your training plan.

```
Garmin Connect ──(garminconnect)──▶ Supabase Postgres ◀──(MCP)── Claude
        ▲                                   │
   GitHub Actions (daily + button)    `coach.*` views + `planned_workouts` table
```

The schema (`users`, `activities`, `splits`, `wellness_log`, `user_sessions`, `sync_log`, `planned_workouts`) is created by the migrations in `migrations/`. It is a single-athlete design: one row in `users`.

> **Unofficial.** Garmin has no public API for personal use. This project uses [`garminconnect`](https://github.com/cyberjunky/python-garminconnect), which is unofficial and can break whenever Garmin changes its login. Use it at your own risk and respect Garmin's terms of service.

## Setup

1. **Supabase.** Create a project (the free plan is enough). Use the *session pooler* connection string (IPv4) from *Project Settings → Database*.
2. **Variables.** Copy `.env.example` to `.env` and fill it in (`DATABASE_URL`). Generate the encryption key with `python -m filipides genkey`. Never commit `.env`.
3. **Install.** `pip install -e .`
4. **Migrations.** `python -m filipides migrate` applies `migrations/*.sql` in order. Migration 003 (`claude_planner` role) is optional; read its header first.
5. **First login (once, on your own computer).** `python -m filipides login` asks for the MFA code and stores the encrypted session in the database.
6. **History.** `python -m filipides sync --days 400 --wellness-days 180` (to build a baseline for HRV and resting HR).
7. **Automate.** In GitHub: *Settings → Secrets and variables → Actions*, create `DATABASE_URL`, `ENCRYPTION_KEY` and, if there is more than one user, `ATHLETE_USER_ID`. The `Sync Garmin` workflow runs daily and has a *Run workflow* button in the Actions tab.

## Commands

| Command | What it does |
|---|---|
| `python -m filipides login` | Log in with MFA and store the encrypted token |
| `python -m filipides sync` | Incremental: new activities + 7 days of wellness |
| `python -m filipides sync --days 400` | Activity history |
| `python -m filipides wellness --days 30` | Wellness only: sleep, HRV, resting HR, body battery, stress, readiness |
| `python -m filipides probe` | Diagnostics: shape of what Garmin returns (no values) |
| `python -m filipides migrate [--only 002]` | Apply migrations |
| `python -m filipides genkey` | Generate an `ENCRYPTION_KEY` |

Runs are idempotent: rerunning a day does not duplicate anything, and if Garmin returns an incomplete day it does not erase what you already had.

## Connecting Claude

- **Supabase MCP (the simple way).** Connect it to this project and paste `docs/claude-project-instructions.md` into your Claude project's instructions. It connects with full project permissions: use it only with your own account and scope it to this project.
- **Least privilege.** Apply migration 003, set a password for `claude_planner` and use a Postgres MCP server with that role: it reads the views and only writes to `planned_workouts`.

The views live in the `coach` schema, which the Supabase REST API does not expose, and every table has RLS enabled with no public policies.

## Security notes

- Everything is stored in **your own** Supabase project. Keep `DATABASE_URL`, `ENCRYPTION_KEY` and your Garmin credentials out of the repository (they belong in `.env` and GitHub Actions secrets).
- If you lose or change `ENCRYPTION_KEY`, the stored session can no longer be decrypted; just run `login` again.
- Your training and health data is personal. Think twice before pointing a third-party tool at this database.

## Known limitations

- The database is accessed with `pg8000` (pure Python) instead of `psycopg`, because on some Windows machines with Smart App Control the native psycopg DLL gets blocked. By default the connection is encrypted without verifying the certificate, like libpq with `sslmode=require`; to verify it, set `DATABASE_SSLMODE=verify-full`.
- If the job fails with "session is no longer valid", run `login` again locally.
- Garmin blocks some datacenter IPs. If GitHub runners fail, run the same command from your computer (e.g. Windows Task Scheduler or cron) or from an always-on machine.
- Supabase's free plan pauses a project after a week of inactivity; the daily job keeps it active.
- `acwr_daily` and `load_approx` are approximations (Garmin's training_load or activity minutes), not TSS or TRIMP.

## Tests

`pytest` (parsing, no network). The database tests are enabled with `TEST_DATABASE_URL` pointing to a **throwaway** Postgres with the migrations applied. They truncate the tables, so never point them at real data.

## License

[MIT](LICENSE)
