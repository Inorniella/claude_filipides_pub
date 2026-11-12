"""Postgres (Supabase) access with pg8000: a 100 % pure-Python driver, no native DLLs.

pg8000 is used instead of psycopg because on some Windows machines (Smart App Control /
App Control) the libpq DLL bundled with psycopg[binary] gets blocked. The `Connection`
class mimics the part of the psycopg API used by the rest of the code:
`conn.execute(sql, params)` returning rows as dicts, plus `commit`, `rollback` and `with`.
"""
import ssl
import uuid
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from urllib.parse import parse_qs, unquote, urlparse

import pg8000.dbapi

from .config import env
from .crypto import decrypt, encrypt


class Result:
    """Query result; rows are returned as dicts {column: value}."""

    def __init__(self, cursor):
        self._cur = cursor
        self._cols = [d[0] for d in cursor.description] if cursor.description else None

    def fetchone(self):
        if self._cols is None:
            return None
        row = self._cur.fetchone()
        return None if row is None else dict(zip(self._cols, row))

    def fetchall(self):
        if self._cols is None:
            return []
        return [dict(zip(self._cols, r)) for r in self._cur.fetchall()]


class Connection:
    def __init__(self, raw):
        self._raw = raw

    def execute(self, sql: str, params=None) -> Result:
        cur = self._raw.cursor()
        cur.execute(sql, tuple(params) if params else ())
        return Result(cur)

    def commit(self) -> None:
        self._raw.commit()

    def rollback(self) -> None:
        self._raw.rollback()

    def close(self) -> None:
        self._raw.close()


def _ssl_context(mode: str, host: str):
    """libpq-style sslmode. `require` encrypts without verifying the certificate (same as
    psycopg by default); `verify-full` also verifies it; `disable` uses no TLS."""
    if mode == "disable":
        return None
    ctx = ssl.create_default_context()
    if mode != "verify-full":
        ctx.check_hostname = False
        ctx.verify_mode = ssl.CERT_NONE
    return ctx


def _connect_kwargs(url: str) -> dict:
    u = urlparse(url)
    q = {k: v[0] for k, v in parse_qs(u.query).items()}
    host = q.get("host") or u.hostname or "localhost"
    port = int(q.get("port") or u.port or 5432)
    local = host.startswith("/") or host in ("localhost", "127.0.0.1")
    mode = env("DATABASE_SSLMODE") or q.get("sslmode") or ("disable" if local else "require")
    kwargs = {
        "user": unquote(u.username or "postgres"),
        "password": unquote(u.password) if u.password else None,
        "database": (u.path or "/postgres").lstrip("/") or "postgres",
        "ssl_context": _ssl_context(mode, host),
    }
    if host.startswith("/"):  # unix socket (local tests)
        kwargs["unix_sock"] = f"{host}/.s.PGSQL.{port}"
    else:
        kwargs["host"], kwargs["port"] = host, port
    return kwargs


@contextmanager
def connect():
    raw = pg8000.dbapi.connect(**_connect_kwargs(env("DATABASE_URL", required=True)))
    conn = Connection(raw)
    try:
        yield conn
        conn.commit()
    except BaseException:
        conn.rollback()
        raise
    finally:
        conn.close()


def resolve_user_id(conn) -> str:
    """Athlete UUID: ATHLETE_USER_ID or the only user in the users table."""
    explicit = env("ATHLETE_USER_ID")
    if explicit:
        return explicit
    rows = conn.execute("SELECT id FROM users ORDER BY created_at LIMIT 2").fetchall()
    if len(rows) != 1:
        raise RuntimeError(
            f"Found {len(rows)} rows in the users table (expected exactly 1); set ATHLETE_USER_ID."
        )
    return str(rows[0]["id"])


# ── Garmin session (encrypted token) ────────────────────────────────────────
SESSION_TTL = timedelta(days=30)


def load_garmin_token(conn, user_id: str) -> str | None:
    row = conn.execute(
        "SELECT token_enc FROM user_sessions WHERE user_id = %s AND source = 'garmin'",
        (user_id,),
    ).fetchone()
    if not row:
        return None
    token = decrypt(row["token_enc"])
    return token or None


def save_garmin_token(conn, user_id: str, token_str: str) -> None:
    now = datetime.now(timezone.utc).replace(tzinfo=None)
    conn.execute(
        """
        INSERT INTO user_sessions (user_id, source, token_enc, expires_at, created_at, updated_at)
        VALUES (%s, 'garmin', %s, %s, %s, %s)
        ON CONFLICT (user_id, source) DO UPDATE
           SET token_enc = EXCLUDED.token_enc,
               expires_at = EXCLUDED.expires_at,
               updated_at = EXCLUDED.updated_at
        """,
        (user_id, encrypt(token_str), now + SESSION_TTL, now, now),
    )


# ── Activities and splits ────────────────────────────────────────────────────
ACTIVITY_COLS = (
    "source_id", "source", "start_time", "name", "sport", "distance_km", "duration_sec",
    "avg_pace_min_km", "avg_hr", "max_hr", "calories", "elevation_gain_m", "avg_cadence",
    "training_load", "vo2max_estimate", "avg_temperature", "avg_power_watts", "avg_swolf",
    "raw_json",
)


def upsert_activity(conn, user_id: str, act: dict) -> tuple[str, bool]:
    """Insert or update an activity. Returns (activity_id, is_new).

    The UUID is generated here: `activities.id` has no DEFAULT in the schema.
    """
    cols = ", ".join(ACTIVITY_COLS)
    placeholders = ", ".join(["%s"] * len(ACTIVITY_COLS))
    updates = ", ".join(
        f"{c} = EXCLUDED.{c}" for c in ACTIVITY_COLS if c not in ("source_id", "source")
    )
    row = conn.execute(
        f"""
        INSERT INTO activities (id, user_id, {cols}, synced_at)
        VALUES (%s, %s, {placeholders}, NOW())
        ON CONFLICT (user_id, source_id, source) DO UPDATE SET {updates}, synced_at = NOW()
        RETURNING id, (xmax = 0) AS inserted
        """,
        (str(uuid.uuid4()), user_id, *[act[c] for c in ACTIVITY_COLS]),
    ).fetchone()
    return str(row["id"]), bool(row["inserted"])


def replace_splits(conn, user_id: str, activity_id: str, splits: list[dict]) -> int:
    conn.execute("DELETE FROM splits WHERE activity_id = %s", (activity_id,))
    for sp in splits:
        conn.execute(
            """
            INSERT INTO splits (activity_id, user_id, km, distance_m, duration_sec, pace_min_km,
                                avg_hr, max_hr, avg_cadence, elevation_gain_m, elevation_loss_m,
                                temperature, power_watts)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
            """,
            (activity_id, user_id, sp["km"], sp["distance_m"], sp["duration_sec"],
             sp["pace_min_km"], sp["avg_hr"], sp["max_hr"], sp["avg_cadence"],
             sp["elevation_gain_m"], sp["elevation_loss_m"], sp["temperature"],
             sp["power_watts"]),
        )
    conn.execute(
        "UPDATE activities SET total_splits = %s WHERE id = %s", (len(splits), activity_id)
    )
    return len(splits)


def last_activity_date(conn, user_id: str):
    row = conn.execute(
        "SELECT max(start_time)::date AS d FROM activities WHERE user_id = %s AND source = 'garmin'",
        (user_id,),
    ).fetchone()
    return row["d"] if row else None


# ── Daily wellness ────────────────────────────────────────────────────────
WELLNESS_COLS = (
    "hrv_ms", "sleep_hours", "sleep_score", "rhr_bpm", "body_battery_max",
    "body_battery_min", "stress_avg", "training_readiness",
)


def upsert_wellness(conn, user_id: str, day, values: dict, raw_json: str) -> None:
    cols = ", ".join(WELLNESS_COLS)
    placeholders = ", ".join(["%s"] * len(WELLNESS_COLS))
    # COALESCE: a partial Garmin failure does not erase what we already had for that day.
    updates = ", ".join(
        f"{c} = COALESCE(EXCLUDED.{c}, wellness_log.{c})" for c in WELLNESS_COLS
    )
    conn.execute(
        f"""
        INSERT INTO wellness_log (user_id, date, {cols}, raw, source, updated_at)
        VALUES (%s, %s, {placeholders}, %s::jsonb, 'garmin', NOW())
        ON CONFLICT (user_id, date) DO UPDATE
           SET {updates}, raw = EXCLUDED.raw, source = 'garmin', updated_at = NOW()
        """,
        (user_id, day, *[values.get(c) for c in WELLNESS_COLS], raw_json),
    )


def log_sync(conn, user_id: str, mode: str, from_date: str, fetched: int, new: int,
             splits: int, duration: float, status: str, error: str | None = None) -> None:
    conn.execute(
        """
        INSERT INTO sync_log (user_id, synced_at, source, mode, from_date, activities_fetched,
                              activities_new, splits_saved, duration_sec, status, error)
        VALUES (%s, NOW(), 'garmin', %s, %s, %s, %s, %s, %s, %s, %s)
        """,
        (user_id, mode, from_date, fetched, new, splits, duration, status, error),
    )
