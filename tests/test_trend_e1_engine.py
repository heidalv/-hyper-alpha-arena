# -*- coding: utf-8 -*-
"""trend_e1_engine 纯逻辑单测（不连库）：配置解析、E1 仓位识别、长车道独占闸。"""
from __future__ import annotations

import json
import os
import sys
import pathlib

import pytest

ROOT = pathlib.Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from backend.services import trend_e1_engine as e1  # noqa: E402


@pytest.fixture(autouse=True)
def _clean_env(monkeypatch):
    for k in list(os.environ):
        if k.startswith("TREND_E1_"):
            monkeypatch.delenv(k, raising=False)
    yield


def test_config_parsing(monkeypatch):
    assert e1.e1_enabled() is False
    assert e1.e1_account_ids() == []
    assert e1.bucket_fraction() == pytest.approx(0.60)
    monkeypatch.setenv("TREND_E1_ENABLED", "true")
    monkeypatch.setenv("TREND_E1_ACCOUNT_IDS", "14, 14, x, 0, 22")
    monkeypatch.setenv("TREND_E1_BUCKET_FRACTION", "0.5")
    assert e1.e1_enabled() is True
    assert e1.e1_account_ids() == [14, 22]
    assert e1.bucket_fraction() == pytest.approx(0.5)


def test_long_lane_exclusive_follows_enabled(monkeypatch):
    assert e1.long_lane_exclusive() is False
    monkeypatch.setenv("TREND_E1_ENABLED", "true")
    assert e1.long_lane_exclusive() is True
    monkeypatch.setenv("TREND_E1_LONG_LANE_EXCLUSIVE", "false")
    assert e1.long_lane_exclusive() is False


def test_is_e1_position_variants():
    assert e1.is_e1_position({"exit_state": {"entry_source": "trend_e1"}})
    assert e1.is_e1_position({"exit_state_json": json.dumps({"entry_source": "trend_e1"})})
    assert e1.is_e1_position({"metadata_json": json.dumps({"entry_source": "trend_e1"})})
    assert not e1.is_e1_position({"exit_state": {"entry_source": "factor_route"}})
    assert not e1.is_e1_position({})

    class Orm:
        exit_state_json = json.dumps({"entry_source": "trend_e1", "structural_stop_price": 1.0})
        metadata_json = None

    assert e1.is_e1_position(Orm())


def test_long_lane_open_gate(monkeypatch):
    # 未启用 → 一律放行
    assert e1.long_lane_open_allowed("long", "position", None)[0] is True
    monkeypatch.setenv("TREND_E1_ENABLED", "true")
    ok, reason = e1.long_lane_open_allowed("long", "position", None)
    assert ok is False and "E1" in reason
    # E1 自己的开仓放行
    assert e1.long_lane_open_allowed("long", "trend_follow", {"entry_source": "trend_e1"})[0] is True
    # 其他车道不受影响
    assert e1.long_lane_open_allowed("short", "scalp", None)[0] is True
    assert e1.long_lane_open_allowed("mid", "swing", None)[0] is True
    # 平仓/减仓不管
    assert e1.long_lane_open_allowed("long", "position", None, add_type="reduce")[0] is True
    # nature 推断出 long 车道也拦
    assert e1.long_lane_open_allowed(None, "trend_follow", None)[0] is False


def test_base_and_slim_drift():
    assert e1._base("BTCUSDT") == "BTC" and e1._base("ETH-USD") == "ETH" and e1._base("SOL") == "SOL"
    d = e1._slim_drift({"trend_drift": 1, "missing": ["SOL"], "extra": [{"symbol": "X"}], "wrong_side": []})
    assert d["trend_drift"] == 1 and d["extra"] == ["X"] and d["missing"] == ["SOL"]
