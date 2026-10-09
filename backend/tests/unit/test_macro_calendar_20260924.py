# -*- coding: utf-8 -*-
"""[2026-09-24 R1-宏观] 宏观事件日历闸单测。

证据来源：`scripts/audit_macro_event_impact_20260924.py`（2026-01-15~09-23，1h K 线，12 币）
  - 事件前 24h 与后 24h 收益符号相反比率：基线 52%、FOMC 36%、非农 39%、CPI 64%；
  - **CPI 且事件前 24h 已动 >3%：18/20 = 90% 反转** ← 本闸唯一依据；
  - 事件窗口波动比不固定（2/6 非农 3.2~4.0 倍 vs 8/12 CPI 0.51）⇒ 不能"逢事件日必拦"。
日历来源：美联储官方 fomccalendars（2026 FOMC：… Sep15-16 · Oct27-28 · Dec8-9）、
         BLS/Guggenheim 2026 美国经济日历（非农/CPI/PPI 日期）。
"""
from __future__ import annotations

import datetime as dt

import pytest

from backend.services import macro_calendar as mc

CST = dt.timezone(dt.timedelta(hours=8))


def _ts(y, m, d, hh, mm=0):
    return int(dt.datetime(y, m, d, hh, mm, tzinfo=CST).timestamp())


@pytest.fixture(autouse=True)
def _clean(monkeypatch):
    for k in ("MACRO_EVENT_GUARD_ENABLED", "MACRO_EVENT_KINDS", "MACRO_EVENT_PRE_H",
              "MACRO_EVENT_REVERSAL_PRE_MOVE_PCT", "MACRO_EVENT_SHRINK_MULT", "MACRO_EVENT_VETO"):
        monkeypatch.delenv(k, raising=False)
    yield


def test_calendar_contains_official_2026_dates():
    evs = {(e["kind"], e["date"]) for e in mc.all_events()}
    # FOMC 官方 2026 场次
    for d in ("2026-01-28", "2026-03-18", "2026-04-29", "2026-06-17",
              "2026-07-29", "2026-09-16", "2026-10-28", "2026-12-09"):
        assert ("fomc", d) in evs
    # 非农 / CPI（2026 经济日历）
    assert ("nfp", "2026-09-04") in evs and ("nfp", "2026-10-02") in evs
    assert ("cpi", "2026-09-11") in evs and ("cpi", "2026-10-14") in evs


def test_fomc_release_time_is_next_day_0200_cst():
    fomc = [e for e in mc.all_events() if e["kind"] == "fomc" and e["date"] == "2026-09-16"][0]
    assert fomc["ts"] == _ts(2026, 9, 17, 2, 0)


def test_nfp_release_time_is_2030_cst():
    nfp = [e for e in mc.all_events() if e["kind"] == "nfp" and e["date"] == "2026-10-02"][0]
    assert nfp["ts"] == _ts(2026, 10, 2, 20, 30)


def test_guard_off_by_default():
    ok, reason, detail = mc.entry_guard("BTC", "buy", {"BTC": {"price_change_24h_pct": 9.0}},
                                        now=_ts(2026, 10, 14, 12, 0))
    assert ok and reason == "macro_guard_off" and detail == {}


def test_guard_blocks_shrink_when_chasing_into_cpi(monkeypatch):
    """CPI 前 8.5h、24h 已涨 +6% 做多 → 缩仓 0.25（依据：18/20 反转）。"""
    monkeypatch.setenv("MACRO_EVENT_GUARD_ENABLED", "true")
    ok, reason, detail = mc.entry_guard("BTC", "buy", {"BTC": {"price_change_24h_pct": 6.0}},
                                        now=_ts(2026, 10, 14, 12, 0))
    assert ok and detail["paper_shrink_mult"] == 0.25
    assert "CPI" in reason and "6.0" in reason and "涨" in reason


def test_guard_allows_small_pre_move(monkeypatch):
    """24h 仅 +1.2%（< 3% 阈值）→ 不介入。"""
    monkeypatch.setenv("MACRO_EVENT_GUARD_ENABLED", "true")
    ok, reason, detail = mc.entry_guard("BTC", "buy", {"BTC": {"price_change_24h_pct": 1.2}},
                                        now=_ts(2026, 10, 14, 12, 0))
    assert ok and "paper_shrink_mult" not in detail


def test_guard_allows_counter_trend_entry(monkeypatch):
    """24h 已跌 −5% 却要做多（逆势）= 不是在追已走完的方向 → 不介入。"""
    monkeypatch.setenv("MACRO_EVENT_GUARD_ENABLED", "true")
    ok, reason, detail = mc.entry_guard("BTC", "buy", {"BTC": {"price_change_24h_pct": -5.0}},
                                        now=_ts(2026, 10, 14, 12, 0))
    assert ok and "paper_shrink_mult" not in detail


def test_guard_ignores_fomc_and_nfp(monkeypatch):
    """FOMC/非农翻转率低于基线（延续性）→ 默认不拦（避免无依据压制开仓）。"""
    monkeypatch.setenv("MACRO_EVENT_GUARD_ENABLED", "true")
    # 2026-10-28 FOMC 决议在 10-29 02:00 CST；取 10-28 20:00 处于前窗口内
    ok, reason, detail = mc.entry_guard("BTC", "buy", {"BTC": {"price_change_24h_pct": 8.0}},
                                        now=_ts(2026, 10, 28, 20, 0))
    assert ok and "paper_shrink_mult" not in detail


def test_guard_outside_pre_window(monkeypatch):
    monkeypatch.setenv("MACRO_EVENT_GUARD_ENABLED", "true")
    monkeypatch.setenv("MACRO_EVENT_PRE_H", "12")
    # 距 CPI 24h 以上
    ok, reason, detail = mc.entry_guard("BTC", "buy", {"BTC": {"price_change_24h_pct": 9.0}},
                                        now=_ts(2026, 10, 12, 12, 0))
    assert ok and "paper_shrink_mult" not in detail


def test_guard_veto_mode(monkeypatch):
    monkeypatch.setenv("MACRO_EVENT_GUARD_ENABLED", "true")
    monkeypatch.setenv("MACRO_EVENT_VETO", "true")
    ok, reason, _ = mc.entry_guard("BTC", "buy", {"BTC": {"price_change_24h_pct": 6.0}},
                                   now=_ts(2026, 10, 14, 12, 0))
    assert not ok and "macro_event_veto" in reason


def test_guard_fail_open_without_data(monkeypatch):
    monkeypatch.setenv("MACRO_EVENT_GUARD_ENABLED", "true")
    ok, reason, _ = mc.entry_guard("BTC", "buy", {}, now=_ts(2026, 10, 14, 12, 0))
    assert ok and "fail-open" in reason


def test_describe_mentions_neighbors():
    s = mc.describe(now=_ts(2026, 9, 24, 10, 0))
    assert "CPI" in s or "非农" in s or "美联储" in s
