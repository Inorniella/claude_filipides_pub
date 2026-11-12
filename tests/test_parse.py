from filipides import garmin


def test_normalize_activity_basic():
    act = {
        "activityId": 123, "activityName": "Mountain Trail", "startTimeLocal": "2026-10-04 09:15:00",
        "activityType": {"typeKey": "trail_running"}, "distance": 21500.0, "duration": 7200.4,
        "averageSpeed": 2.986, "averageHR": 151.6, "maxHR": 178, "elevationGain": 940.0,
        "activityTrainingLoad": 210.5,
    }
    n = garmin.normalize_activity(act)
    assert n["source_id"] == "123" and n["sport"] == "trail_running"
    assert n["distance_km"] == 21.5 and n["duration_sec"] == 7200
    assert n["avg_hr"] == 152 and n["avg_pace_min_km"] == 5.58
    assert n["start_time"].year == 2026


def test_parse_splits_skips_short_laps():
    detail = {"lapDTOs": [{"distance": 1000, "duration": 300, "averageSpeed": 1000 / 300},
                          {"distance": 40, "duration": 20}]}
    s = garmin.parse_splits(detail)
    assert len(s) == 1 and s[0]["pace_min_km"] == 5.0


def test_parse_wellness_full():
    sleep = {"dailySleepDTO": {"sleepTimeSeconds": 27000, "sleepScores": {"overall": {"value": 82}}},
             "restingHeartRate": 49}
    hrv = {"hrvSummary": {"lastNightAvg": 61}}
    summary = {"restingHeartRate": 47, "bodyBatteryHighestValue": 92, "bodyBatteryLowestValue": 18,
               "averageStressLevel": 27}
    readiness = [{"score": 73}]
    w = garmin.parse_wellness(sleep, hrv, summary, readiness)
    assert w == {"hrv_ms": 61.0, "sleep_hours": 7.5, "sleep_score": 82, "rhr_bpm": 47,
                 "body_battery_max": 92, "body_battery_min": 18, "stress_avg": 27,
                 "training_readiness": 73}


def test_parse_wellness_missing_everything():
    w = garmin.parse_wellness(None, None, None, [])
    assert all(v is None for v in w.values())


def test_parse_wellness_fallbacks():
    # HRV without summary -> sleep avgOvernightHrv; resting HR without summary -> the sleep one
    w = garmin.parse_wellness({"avgOvernightHrv": 58.4, "restingHeartRate": 50}, None, {}, None)
    assert w["hrv_ms"] == 58.4 and w["rhr_bpm"] == 50


def test_describe_shape_hides_values():
    shape = garmin.describe_shape({"a": 5, "b": {"c": "secret"}, "d": [1, 2], "e": None})
    assert shape == {"a": "int", "b": {"c": "str"}, "d": ["list[2]", "int"], "e": "None"}


def test_probe_reports_errors_and_shapes():
    from datetime import date

    class Fake:
        def get_sleep_data(self, d): return {"dailySleepDTO": {"sleepTimeSeconds": 25000}}
        def get_hrv_data(self, d): raise RuntimeError("boom")
        def get_user_summary(self, d): return {}
        def get_training_readiness(self, d): return []

    garmin.PAUSE = 0
    r = garmin.probe_wellness(Fake(), date(2026, 11, 8))
    assert r["sleep"]["ok"] and not r["hrv"]["ok"] and "boom" in r["hrv"]["error"]
    assert r["parsed_has_value"]["sleep_hours"] is True
    assert r["parsed_has_value"]["hrv_ms"] is False


def test_parse_wellness_negative_means_no_data():
    # Garmin returns -1 for stress (and sometimes other fields) when there is no measurement
    w = garmin.parse_wellness({}, {}, {"averageStressLevel": -1, "restingHeartRate": -1,
                                       "bodyBatteryHighestValue": -1}, [{"score": -1}])
    assert all(v is None for v in w.values())


def test_parse_wellness_keeps_zero_battery():
    w = garmin.parse_wellness({}, {}, {"bodyBatteryLowestValue": 0, "averageStressLevel": 0}, [])
    assert w["body_battery_min"] == 0 and w["stress_avg"] == 0
