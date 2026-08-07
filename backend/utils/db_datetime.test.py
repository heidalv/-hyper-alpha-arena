"""Tests for db_datetime helpers."""
from datetime import datetime, timezone, timedelta

from backend.utils.db_datetime import db_naive_to_utc_iso, DB_NAIVE_TZ


def test_db_naive_beijing_serializes_to_utc_iso():
    # PG TIMESTAMP naive: 13:51 Beijing wall clock
    naive = datetime(2026, 6, 9, 13, 51, 38)
    iso = db_naive_to_utc_iso(naive)
    assert iso == "2026-06-09T05:51:38+00:00"
    parsed = datetime.fromisoformat(iso)
    assert parsed.astimezone(DB_NAIVE_TZ).strftime("%H:%M") == "13:51"


def test_aware_datetime_passthrough():
    aware = datetime(2026, 6, 9, 5, 51, 38, tzinfo=timezone.utc)
    iso = db_naive_to_utc_iso(aware)
    assert iso.startswith("2026-06-09T05:51:38")


def test_parse_db_naive_beijing_to_utc():
    from backend.utils.db_datetime import parse_db_naive_to_utc

    # PG TIMESTAMP naive = 北京时间 13:41
    utc = parse_db_naive_to_utc(datetime(2026, 6, 9, 13, 41, 5))
    assert utc is not None
    assert utc.hour == 5 and utc.minute == 41

    # API ISO with offset
    utc2 = parse_db_naive_to_utc("2026-06-09T05:41:05+00:00")
    assert utc2 is not None
    assert utc2.hour == 5
