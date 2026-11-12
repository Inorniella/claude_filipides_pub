"""Garmin Connect client: token-based session, activities, splits and wellness.

The `normalize_*` and `parse_*` functions are pure (dict -> dict) so they can be tested
without network access. garminconnect endpoints return different JSON depending on the
device and firmware, so all parsing is defensive: a missing field is None, never an error.
"""
import json
import logging
import time
from datetime import date, datetime, timedelta

logger = logging.getLogger("filipides.garmin")

PAUSE = 0.5  # seconds between requests, to avoid hammering the unofficial API

ACTIVITY_TYPES = {
    "running", "trail_running", "treadmill_running",
    "cycling", "road_biking", "indoor_cycling", "virtual_ride",
    "gravel_cycling", "mountain_biking",
    "lap_swimming", "open_water_swimming", "swimming",
}


# ── Session ──────────────────────────────────────────────────────────────────
def login_interactive(email: str, password: str):
    """First login. Prompts for the MFA code on the console if Garmin requires it."""
    from garminconnect import Garmin

    client = Garmin(email, password, prompt_mfa=lambda: input("Garmin MFA code: ").strip())
    client.login()
    return client


def client_from_token(token_str: str):
    """Restore the session from the serialized token. Returns None if it is no longer valid."""
    from garminconnect import Garmin

    try:
        client = Garmin()
        client.login(token_str)
        client.get_user_summary(date.today().isoformat())  # minimal sanity check
        return client
    except Exception as exc:  # expired or revoked token
        logger.warning("The stored token is not usable (%s). Run `filipides login` again.", exc)
        return None


def dump_token(client) -> str:
    """Current token (it may have been refreshed during the session)."""
    return client.client.dumps()


# ── Parsing helpers ───────────────────────────────────────────────────────
def _dig(data, *path):
    for key in path:
        if isinstance(data, list):
            data = data[0] if data else None
        if not isinstance(data, dict):
            return None
        data = data.get(key)
    return data


def _int(value):
    return None if value is None else int(round(float(value)))


def _measure(value):
    """Garmin uses negative values (e.g. -1 for stress) to mean 'no measurement'."""
    if value is None:
        return None
    number = float(value)
    return None if number < 0 else number


def pace_from_speed(speed_m_s):
    if not speed_m_s:
        return 0.0
    return round(1000 / (speed_m_s * 60), 2)


# ── Activities ─────────────────────────────────────────────────────────────
def normalize_activity(act: dict) -> dict:
    start = act.get("startTimeLocal")
    return {
        "source_id": str(act.get("activityId", "")),
        "source": "garmin",
        "start_time": datetime.fromisoformat(start[:19]) if start else datetime.utcnow(),
        "name": act.get("activityName", ""),
        "sport": _dig(act, "activityType", "typeKey") or "",
        "distance_km": round((act.get("distance") or 0) / 1000, 3),
        "duration_sec": int(act.get("duration") or 0),
        "avg_pace_min_km": pace_from_speed(act.get("averageSpeed") or 0),
        "avg_hr": _int(act.get("averageHR")),
        "max_hr": _int(act.get("maxHR")),
        "calories": _int(act.get("calories")),
        "elevation_gain_m": act.get("elevationGain"),
        "avg_cadence": _int(act.get("averageRunningCadenceInStepsPerMinute")),
        "training_load": act.get("activityTrainingLoad"),
        "vo2max_estimate": act.get("vO2MaxValue"),
        "avg_temperature": act.get("averageTemperature"),
        "avg_power_watts": _int(act.get("avgPower")),
        "avg_swolf": act.get("avgStrokes"),
        "raw_json": json.dumps(act, ensure_ascii=False),
    }


def parse_splits(detail: dict) -> list[dict]:
    splits = []
    laps = (detail or {}).get("lapDTOs") or (detail or {}).get("laps") or []
    for i, lap in enumerate(laps):
        dist = lap.get("distance") or 0
        if dist < 100:
            continue
        splits.append({
            "km": i + 1,
            "distance_m": round(dist, 1),
            "duration_sec": round(lap.get("duration") or 0, 1),
            "pace_min_km": pace_from_speed(lap.get("averageSpeed") or 0),
            "avg_hr": _int(lap.get("averageHR") or lap.get("avgHr")),
            "max_hr": _int(lap.get("maxHR") or lap.get("maxHr")),
            "avg_cadence": _int(lap.get("averageRunCadence")),
            "elevation_gain_m": lap.get("elevationGain"),
            "elevation_loss_m": lap.get("elevationLoss"),
            "temperature": lap.get("averageTemperature"),
            "power_watts": _int(lap.get("avgPower")),
        })
    return splits


def fetch_activities(client, from_date: str, limit: int = 1000) -> list[dict]:
    raw = client.get_activities_by_date(
        startdate=from_date, enddate=date.today().isoformat(), activitytype=None
    )
    out = []
    for act in raw:
        if _dig(act, "activityType", "typeKey") not in ACTIVITY_TYPES:
            continue
        out.append(normalize_activity(act))
        if len(out) >= limit:
            break
    return out


def fetch_splits(client, source_id: str) -> list[dict]:
    time.sleep(PAUSE)
    try:
        splits = parse_splits(client.get_activity_splits(source_id))
        if not splits:
            splits = parse_splits(client.get_activity_details(source_id))
        return splits
    except Exception as exc:
        logger.warning("Splits not available for %s: %s", source_id, exc)
        return []


# ── Daily wellness ────────────────────────────────────────────────────────
def parse_wellness(sleep: dict | None, hrv: dict | None, summary: dict | None,
                   readiness) -> dict:
    """Combine the four Garmin responses into one wellness_log row."""
    sleep_seconds = _dig(sleep, "dailySleepDTO", "sleepTimeSeconds")
    hrv_ms = _dig(hrv, "hrvSummary", "lastNightAvg")
    if hrv_ms is None:
        hrv_ms = _dig(sleep, "avgOvernightHrv")
    rhr = _dig(summary, "restingHeartRate")
    if rhr is None:
        rhr = _dig(sleep, "restingHeartRate")
    hrv_ms = _measure(hrv_ms)
    return {
        "hrv_ms": hrv_ms if hrv_ms else None,
        "sleep_hours": round(sleep_seconds / 3600, 2) if sleep_seconds and sleep_seconds > 0 else None,
        "sleep_score": _int(_measure(_dig(sleep, "dailySleepDTO", "sleepScores", "overall", "value"))),
        "rhr_bpm": _int(_measure(rhr)),
        "body_battery_max": _int(_measure(_dig(summary, "bodyBatteryHighestValue"))),
        "body_battery_min": _int(_measure(_dig(summary, "bodyBatteryLowestValue"))),
        "stress_avg": _int(_measure(_dig(summary, "averageStressLevel"))),
        "training_readiness": _int(_measure(_dig(readiness, "score"))),
    }


def fetch_wellness_day(client, day: date) -> tuple[dict, dict]:
    """Return (values, raw). Each endpoint fails separately without taking down the day."""
    iso = day.isoformat()
    calls = {
        "sleep": client.get_sleep_data,
        "hrv": client.get_hrv_data,
        "summary": client.get_user_summary,
        "readiness": client.get_training_readiness,
    }
    raw = {}
    for name, fn in calls.items():
        time.sleep(PAUSE)
        try:
            raw[name] = fn(iso)
        except Exception as exc:
            if _is_fatal(exc):
                raise
            logger.warning("Garmin %s %s failed: %s: %s", name, iso, type(exc).__name__, str(exc)[:160])
            raw[name] = None
    values = parse_wellness(raw["sleep"], raw["hrv"], raw["summary"], raw["readiness"])
    return values, raw


def _is_fatal(exc: Exception) -> bool:
    """Authentication or rate limit: continuing to call only makes things worse."""
    from garminconnect import GarminConnectAuthenticationError, GarminConnectTooManyRequestsError

    return isinstance(exc, (GarminConnectAuthenticationError, GarminConnectTooManyRequestsError))


def daterange(days: int):
    today = date.today()
    for offset in range(days - 1, -1, -1):
        yield today - timedelta(days=offset)


# ── Diagnostics ─────────────────────────────────────────────────────────────
def describe_shape(obj, depth: int = 2):
    """Shape of a Garmin response (keys and types), without any values."""
    if obj is None:
        return "None"
    if isinstance(obj, dict):
        if depth <= 0:
            return f"dict[{len(obj)} keys]"
        return {k: describe_shape(v, depth - 1) for k, v in obj.items()}
    if isinstance(obj, list):
        if not obj:
            return "list[0]"
        return [f"list[{len(obj)}]", describe_shape(obj[0], depth - 1)]
    return type(obj).__name__


def probe_wellness(client, day: date) -> dict:
    """Call each wellness endpoint and summarize what it returned, without exposing values."""
    iso = day.isoformat()
    calls = {
        "sleep": client.get_sleep_data,
        "hrv": client.get_hrv_data,
        "summary": client.get_user_summary,
        "readiness": client.get_training_readiness,
    }
    raw, report = {}, {}
    for name, fn in calls.items():
        time.sleep(PAUSE)
        try:
            raw[name] = fn(iso)
            report[name] = {"ok": True, "shape": describe_shape(raw[name])}
        except Exception as exc:
            raw[name] = None
            report[name] = {"ok": False, "error": f"{type(exc).__name__}: {str(exc)[:200]}"}
    parsed = parse_wellness(raw["sleep"], raw["hrv"], raw["summary"], raw["readiness"])
    report["parsed_has_value"] = {k: v is not None for k, v in parsed.items()}
    return report
