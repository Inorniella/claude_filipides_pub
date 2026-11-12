"""Require a Postgres database with the migrations applied. Skipped without TEST_DATABASE_URL."""
import datetime as dt
import os

import pytest

pytestmark = pytest.mark.skipif(not os.environ.get("TEST_DATABASE_URL"), reason="TEST_DATABASE_URL not set")


@pytest.fixture()
def conn(monkeypatch):
    monkeypatch.setenv("DATABASE_URL", os.environ["TEST_DATABASE_URL"])
    from cryptography.fernet import Fernet
    monkeypatch.setenv("ENCRYPTION_KEY", Fernet.generate_key().decode())
    from filipides import db
    with db.connect() as c:
        c.execute("TRUNCATE sync_log, user_sessions, splits, activities, wellness_log, planned_workouts, users RESTART IDENTITY CASCADE")
        c.execute("INSERT INTO users DEFAULT VALUES")  # the single athlete
        c.commit()
        yield c
        c.rollback()


def _act(i="1", dist=10.0):
    from filipides.garmin import normalize_activity
    return normalize_activity({"activityId": i, "startTimeLocal": "2026-10-04 09:00:00",
                               "activityType": {"typeKey": "running"}, "distance": dist * 1000, "duration": 3000})


def test_activity_upsert_is_idempotent(conn):
    from filipides import db
    uid = db.resolve_user_id(conn)
    a1, new1 = db.upsert_activity(conn, uid, _act())
    a2, new2 = db.upsert_activity(conn, uid, _act(dist=12.0))
    assert a1 == a2 and new1 is True and new2 is False
    assert conn.execute("SELECT distance_km FROM activities").fetchone()["distance_km"] == 12.0


def test_splits_replace(conn):
    from filipides import db
    uid = db.resolve_user_id(conn)
    aid, _ = db.upsert_activity(conn, uid, _act())
    splits = [{"km": 1, "distance_m": 1000, "duration_sec": 300, "pace_min_km": 5.0, "avg_hr": 150,
               "max_hr": 160, "avg_cadence": 170, "elevation_gain_m": 5, "elevation_loss_m": 3,
               "temperature": 12, "power_watts": None}]
    assert db.replace_splits(conn, uid, aid, splits) == 1
    assert db.replace_splits(conn, uid, aid, splits * 2) == 2
    assert conn.execute("SELECT count(*) AS n FROM splits").fetchone()["n"] == 2


def test_wellness_partial_update_keeps_old_values(conn):
    from filipides import db
    uid = db.resolve_user_id(conn)
    day = dt.date(2026, 11, 1)
    db.upsert_wellness(conn, uid, day, {"hrv_ms": 60.0, "rhr_bpm": 48}, "{}")
    db.upsert_wellness(conn, uid, day, {"sleep_hours": 7.0}, "{}")  # Garmin only returned sleep
    row = conn.execute("SELECT hrv_ms, rhr_bpm, sleep_hours FROM wellness_log").fetchone()
    assert row == {"hrv_ms": 60.0, "rhr_bpm": 48, "sleep_hours": 7.0}


def test_token_roundtrip_is_encrypted(conn):
    from filipides import db
    uid = db.resolve_user_id(conn)
    db.save_garmin_token(conn, uid, "secret-token")
    stored = conn.execute("SELECT token_enc FROM user_sessions").fetchone()["token_enc"]
    assert "secret-token" not in stored
    assert db.load_garmin_token(conn, uid) == "secret-token"
    db.save_garmin_token(conn, uid, "another")  # upsert
    assert db.load_garmin_token(conn, uid) == "another"


def test_planned_workout_default_user(conn):
    conn.execute("INSERT INTO planned_workouts (planned_date, title) VALUES (current_date, 'x')")
    assert conn.execute("SELECT user_id IS NOT NULL AS ok FROM planned_workouts").fetchone()["ok"]
