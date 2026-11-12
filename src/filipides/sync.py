"""Orchestration: Garmin -> Postgres. Idempotent; safe to rerun without duplicating anything."""
import json
import logging
import time
from datetime import date, timedelta

from . import db, garmin

logger = logging.getLogger("filipides.sync")


def _client_for(conn, user_id: str):
    token = db.load_garmin_token(conn, user_id)
    if not token:
        raise RuntimeError("No Garmin session stored. Run `python -m filipides login`.")
    client = garmin.client_from_token(token)
    if client is None:
        raise RuntimeError("The Garmin session is no longer valid. Run `python -m filipides login`.")
    return client


def _persist_refreshed_token(user_id: str, client) -> None:
    """Garmin renews the token during the session: saving the new one avoids full logins."""
    try:
        token = garmin.dump_token(client)
        with db.connect() as conn:
            db.save_garmin_token(conn, user_id, token)
    except Exception as exc:
        logger.warning("Could not save the refreshed token: %s", exc)


def sync_activities(days: int | None = None, with_splits: bool = True) -> dict:
    """days=None -> incremental since the last stored activity (with a 3-day margin).

    Database connections are short-lived: they are opened only to save, because
    Supabase closes idle ones while we wait for Garmin.
    """
    started = time.time()
    with db.connect() as conn:
        user_id = db.resolve_user_id(conn)
        client = _client_for(conn, user_id)
        if days is not None:
            from_date = (date.today() - timedelta(days=days)).isoformat()
            mode = "history"
        else:
            last = db.last_activity_date(conn, user_id)
            from_date = (last - timedelta(days=3) if last else date.today() - timedelta(days=365)).isoformat()
            mode = "incremental"

    fetched = new = splits_saved = 0
    status, error = "ok", None
    try:
        activities = garmin.fetch_activities(client, from_date)
        fetched = len(activities)
        for act in activities:
            with db.connect() as conn:
                activity_id, inserted = db.upsert_activity(conn, user_id, act)
            new += inserted
            # Splits only for new activities: avoids hundreds of calls on every run.
            if with_splits and inserted:
                splits = garmin.fetch_splits(client, act["source_id"])  # no connection held open
                with db.connect() as conn:
                    splits_saved += db.replace_splits(conn, user_id, activity_id, splits)
    except Exception as exc:
        status, error = "error", str(exc)[:500]
        raise
    finally:
        _persist_refreshed_token(user_id, client)
        with db.connect() as conn:
            db.log_sync(conn, user_id, mode, from_date, fetched, new, splits_saved,
                        round(time.time() - started, 1), status, error)
    return {"mode": mode, "from": from_date, "fetched": fetched, "new": new, "splits": splits_saved}


def sync_wellness(days: int = 7) -> dict:
    """Fetch sleep, HRV, resting HR, body battery, stress and readiness for the last N days."""
    saved = empty = failed = 0
    with db.connect() as conn:
        user_id = db.resolve_user_id(conn)
        client = _client_for(conn, user_id)

    logger.info("Wellness: %d days, 4 requests per day; may take several minutes. "
                "Each day is saved as it finishes; you can interrupt with Ctrl+C.", days)
    try:
        for done, day in enumerate(garmin.daterange(days), start=1):
            if done % 10 == 0:
                logger.info("Wellness: %d/%d days (saved %d, no data %d, failed %d)",
                            done, days, saved, empty, failed)
            values, raw = garmin.fetch_wellness_day(client, day)
            if all(v is None for v in values.values()):
                empty += 1  # day without data (watch not synced or tracking off)
                continue
            try:
                with db.connect() as conn:  # short connection: only while saving
                    db.upsert_wellness(conn, user_id, day, values, json.dumps(raw, ensure_ascii=False))
                saved += 1
            except Exception as exc:  # one problematic day must not take down the rest
                failed += 1
                logger.warning("Could not save wellness for %s: %s", day, str(exc)[:200])
    finally:
        _persist_refreshed_token(user_id, client)
    return {"days_requested": days, "saved": saved, "empty": empty, "failed": failed}
