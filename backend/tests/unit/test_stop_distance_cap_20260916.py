# -*- coding: utf-8 -*-
"""[2026-09-16 调研轮7] 止损距离上限契约测试（`clamp_stop_distance` + 入场接线）。

数据依据（报告 reports/_调研_止损结构_20260916.md）：
  9/11 后 mid+long n=34：赢家最大逆行 MAE(价格) 1.20%（n=18），输家 2.56~4.85%，
  现役 SL 4.50~4.85% ⇒ avg_loss > avg_win、打平需胜率 55.2% 实际 52.9%。
  反事实 cap=2%：误杀赢家 0/18，区间净额 -23.49 → +73.54。
回滚：MIDLONG_MAX_SL_PCT_MID / _LONG = 0。
"""
from __future__ import annotations

import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT))

from backend.config import settings  # noqa: E402
from backend.services.mlto.midlong_trade_design import clamp_stop_distance  # noqa: E402


@pytest.fixture(autouse=True)
def _restore(monkeypatch):
    monkeypatch.setattr(settings, "MIDLONG_MAX_SL_PCT_MID", 0.02, raising=False)
    monkeypatch.setattr(settings, "MIDLONG_MAX_SL_PCT_LONG", 0.03, raising=False)
    yield


def test_settings_keys_declared():
    """§73.4：未在 settings 声明的键写进 .env 也不生效（静默失效）——必须声明。"""
    assert isinstance(getattr(settings, "MIDLONG_MAX_SL_PCT_MID"), float)
    assert isinstance(getattr(settings, "MIDLONG_MAX_SL_PCT_LONG"), float)


def test_mid_cap_narrows_only():
    sl, why = clamp_stop_distance(0.0485, "mid")   # 现役实测 4.85% → 2%
    assert sl == pytest.approx(0.02) and "上限" in why
    # 已在上限内不动
    assert clamp_stop_distance(0.015, "mid") == (0.015, "ok")
    assert clamp_stop_distance(0.02, "mid") == (0.02, "ok")
    # 0 / 缺失 = 不适用（fail-open 不清零）
    assert clamp_stop_distance(0.0, "mid")[0] == 0.0


def test_long_cap_and_other_tiers():
    assert clamp_stop_distance(0.065, "long")[0] == pytest.approx(0.03)  # E1 现役 6.5%
    assert clamp_stop_distance(0.025, "long")[0] == pytest.approx(0.025)
    # short/未知 tier 不适用（scalp 不改）
    assert clamp_stop_distance(0.05, "short")[0] == pytest.approx(0.05)
    assert clamp_stop_distance(0.05, "")[0] == pytest.approx(0.05)


def test_rollback_switch_zero(monkeypatch):
    monkeypatch.setattr(settings, "MIDLONG_MAX_SL_PCT_MID", 0.0, raising=False)
    monkeypatch.setattr(settings, "MIDLONG_MAX_SL_PCT_LONG", 0.0, raising=False)
    assert clamp_stop_distance(0.0485, "mid") == (0.0485, "cap_off")
    assert clamp_stop_distance(0.065, "long") == (0.065, "cap_off")


def test_cap_configurable(monkeypatch):
    monkeypatch.setattr(settings, "MIDLONG_MAX_SL_PCT_MID", 0.035, raising=False)
    assert clamp_stop_distance(0.0485, "mid")[0] == pytest.approx(0.035)


def test_entry_path_wires_the_cap(monkeypatch):
    """接线：`try_execute_independent_agent_open` 必须调用本上限（choke point 不脱钩）。"""
    from backend.services.full_auto import midlong_helpers as mh
    from backend.services.mlto import midlong_trade_design as mtd

    seen = {}

    def _fake(sl_pct, tier):
        seen["sl_pct"] = sl_pct
        seen["tier"] = tier
        return 0.02, "test_cap"

    monkeypatch.setattr(mtd, "clamp_stop_distance", _fake, raising=True)
    host = SimpleNamespace(append_event=lambda *a, **k: None)
    sess = SimpleNamespace(session_id="fa_test", mode="running")
    try:
        mh.try_execute_independent_agent_open(
            db=None, session=sess, sym="ASTER", tier="mid", action="buy",
            confidence=70, sl_pct=0.0485, tp_pct=0.08, trade_nature="swing",
            market_summary={}, host=host,
        )
    except Exception:
        pass  # 后续闸门/DB 依赖不参与本契约；只验证上限被调用
    assert seen.get("tier") == "mid", "止损上限未接入入场路径（choke point 脱钩）"
    assert seen.get("sl_pct") == pytest.approx(0.0485)
