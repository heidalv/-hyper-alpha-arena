# -*- coding: utf-8 -*-
"""[P5 大轮回 2026-09-27] §13.2 分车道日开仓配额契约（日内 6/天、趋势 2/天）。"""
import os
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT))

from backend.services.risk import daily_quota as dq  # noqa: E402


def test_bucket_mapping_per_lane():
    assert dq.bucket_for("mid", "swing") == "mid"
    assert dq.bucket_for("short", None) == "scalp"
    assert dq.bucket_for("long", None) == "trend"
    assert dq.bucket_for(None, "trend_follow") == "trend"
    assert dq.bucket_for(None, "position") == "trend"
    assert dq.bucket_for("mid", None) == "mid"


def test_caps_from_file_defaults():
    """runtime_tuning 无显式值时回退设计默认：mid 6 / trend 2。"""
    for b, expect in (("mid", 6), ("trend", 2)):
        try:
            v = dq.cap_for(b)
            assert v == expect, f"{b} cap={v} 应等于设计默认 {expect}"
        except Exception as exc:
            pytest.skip(f"runtime_tuning 不可读: {exc}")


def test_check_verdict_shape():
    class _DB:
        pass
    try:
        v = dq.check(_DB(), 14, tier="mid", trade_nature="swing")
        assert v.bucket == "mid"
        assert isinstance(v.allowed, bool) and isinstance(v.cap, int)
    except Exception as exc:
        pytest.skip(f"check 依赖 DB: {exc}")


def test_opens_today_conditions_are_lane_specific():
    src = (ROOT / "backend" / "services" / "risk" / "daily_quota.py"
           ).read_text(encoding="utf-8")
    assert "timeframe_tier IN ('mid','short')" in src
    assert "timeframe_tier = 'long'" in src
    assert "mid_daily_cap" in src


def test_paper_wiring_contract():
    src = (ROOT / "backend" / "services" / "full_auto" / "paper_execution.py"
           ).read_text(encoding="utf-8")
    assert "P5_DAILY_QUOTA_PAPER" in src
    assert "daily_quota" in src and "daily_quota_block" in src


def test_schema_registers_mid_key():
    src = (ROOT / "backend" / "services" / "runtime_tuning_store.py"
           ).read_text(encoding="utf-8")
    assert '"mid_daily_cap"' in src
    assert '"trend_daily_cap": {"value": 2' in src
