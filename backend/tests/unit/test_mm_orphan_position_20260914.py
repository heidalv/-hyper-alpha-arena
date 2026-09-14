# -*- coding: utf-8 -*-
"""[2026-09-14 F90] 孤儿持仓生命周期契约。

发现背景：把 DOGE 移出宇宙后，`lane_runtime_state` 里的历史行仍被 `load_states`
读进 `self.states`（实测线上 `states` 里确实有 DOGE 僵尸行），而 tick 主循环、
共享库存账本（净敞口上限）、`fetch_market` 全部只遍历 `self.symbols`。当时 DOGE
恰好空仓所以只是脏数据；**若带仓移除**，该仓位会：
  ① 不计入净敞口/方向上限（风险账本看不见）；
  ② 永远不会被平掉（没有任何代码路径会碰它）；
  ③ 盈亏永不实现（账本与账户权益都对不上真实持仓）。
本文件锁定修复后的三条契约：可见、计入风险、强制退出（且绝不用陈旧价成交）。
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT))

from backend.services.market_maker import runner as mmrunner  # noqa: E402
from backend.services.market_maker.core import LaneRiskLimits, QuoteParams  # noqa: E402


def _runner(symbols=("BTC", "ETH")):
    """不触 DB 的最小 ShadowRunner（__init__ 无 IO）。"""
    return mmrunner.ShadowRunner(
        lane_id="mm_test_orphan", venue="asterdex", symbols=list(symbols),
        equity=300.0, account_id=None, params=QuoteParams(),
        limits=LaneRiskLimits(), fill_notional=300.0,
    )


def _state(symbol: str, qty: float, avg_px: float = 100.0, avg_mid: float = 100.0):
    st = mmrunner.SymbolState(symbol=symbol)
    st.mid_hist = [100.0] * 60
    st.qty, st.avg_px, st.avg_mid = qty, avg_px, avg_mid
    st.opened_ts = 1_000_000.0
    st.last_ts = 1_000_000.0
    return st


NOW = 1_000_000_005.0          # 统一"当前时刻"（秒）


def _row(mid: float = 100.0, half_spread: float = 0.05, age_sec: float = 5.0):
    """行情行：`ts_ms` 为毫秒时间戳，age_sec 表示相对 NOW 的数据年龄。"""
    return {"ts_ms": int((NOW - age_sec) * 1000.0), "mid": mid,
            "half_spread": half_spread}


# ── ① 可见性 ────────────────────────────────────────────────────────────────

def test_flat_orphan_is_not_a_position():
    """空仓的历史币种（线上 DOGE 的情形）不算孤儿持仓，但仍是脏状态。"""
    r = _runner()
    r.states["DOGE"] = _state("DOGE", 0.0)
    assert r.orphan_states() == {}, "空仓不应被当成孤儿持仓"
    assert "DOGE" not in r.risk_symbols()


def test_positional_orphan_is_detected_and_counted_in_risk():
    """带仓的历史币种 ⇒ 被识别为孤儿，且进入风险账本币种集合。"""
    r = _runner()
    r.states["DOGE"] = _state("DOGE", 5000.0)   # 5000 × 100 = $500 > $300 上限
    assert set(r.orphan_states()) == {"DOGE"}
    rs = r.risk_symbols()
    assert "DOGE" in rs, "孤儿持仓必须计入共享风险账本（否则净敞口上限失真）"
    assert rs[:2] == ["BTC", "ETH"], "在营币种顺序不得改变"


def test_prune_flat_orphans_removes_zombie_state():
    """已平孤儿（空仓）从内存清理，避免死币种被每 tick 写回。"""
    r = _runner()
    r.states["DOGE"] = _state("DOGE", 0.0)
    r._seg_watermark["DOGE"] = 123
    dead = r.prune_flat_orphans()
    assert dead == ["DOGE"]
    assert "DOGE" not in r.states and "DOGE" not in r._seg_watermark
    assert set(r.states) == {"BTC", "ETH"}, "在营币种运行态不得被清理"


def test_prune_keeps_positional_orphan_until_flat():
    """带仓孤儿不得被清理（必须先退出、再清理）。"""
    r = _runner()
    r.states["DOGE"] = _state("DOGE", 5000.0)
    assert r.prune_flat_orphans() == []
    assert "DOGE" in r.states


# ── ② 强制退出 ──────────────────────────────────────────────────────────────

def test_orphan_long_exits_at_bid_side_with_taker_fee():
    """多头孤儿 ⇒ 卖出全仓、吃对手价（mid - 半价差）、按 taker 计费。

    退出价必须劣于中间价：否则等于假设我们能免费离场（虚增盈亏）。
    """
    st = _state("DOGE", 5000.0)                    # 名义 $500
    fills, skip, mid = mmrunner.plan_orphan_exit(
        state=st, market_row=_row(mid=100.0, half_spread=0.05),
        now_ts=NOW, taker_fee_bp=5.0)
    assert skip == "orphan_flatten" and len(fills) == 1
    f = fills[0]
    assert f.side == "sell" and f.is_flatten
    assert f.qty == pytest.approx(5000.0), "必须一次平掉全仓（不是部分退出）"
    assert f.px == pytest.approx(99.95), "卖出必须吃对手价（mid - 半价差）"
    assert f.mid == pytest.approx(100.0)
    assert f.fee_usd < 0, "taker 手续费必须计入成本"


def test_orphan_short_exits_at_ask_side():
    """空头孤儿 ⇒ 买入平仓、吃 mid + 半价差。"""
    st = _state("DOGE", -5000.0)
    fills, skip, _ = mmrunner.plan_orphan_exit(
        state=st, market_row=_row(mid=100.0, half_spread=0.05),
        now_ts=NOW, taker_fee_bp=5.0)
    assert skip == "orphan_flatten"
    assert fills[0].side == "buy" and fills[0].px == pytest.approx(100.05)


# ── ③ 陈旧价保护（F89a 幻影成交不再重演）──────────────────────────────────

def test_stale_market_never_exits():
    """行情陈旧 ⇒ 不产生任何成交（绝不用旧价平仓），且给出可见原因。"""
    st = _state("DOGE", 5000.0)
    fills, skip, mid = mmrunner.plan_orphan_exit(
        state=st, market_row=_row(age_sec=3600.0),
        now_ts=NOW, taker_fee_bp=5.0)   # 行情已是 1 小时前
    assert fills == [], "陈旧行情下不得成交（F89a 幻影成交教训）"
    assert skip.startswith("orphan_stale_data")
    assert mid == 0.0


def test_missing_market_and_flat_state_are_noops():
    """无行情 ⇒ orphan_no_market；已平 ⇒ 不重复退出。"""
    r = _runner()
    r.states["DOGE"] = _state("DOGE", 5000.0)
    fills, skip, _ = mmrunner.plan_orphan_exit(
        state=r.states["DOGE"], market_row=None, now_ts=NOW,
        taker_fee_bp=5.0)
    assert fills == [] and skip == "orphan_no_market"
    assert set(r.orphan_states()) == {"DOGE"}, "未退出前孤儿持仓必须继续可见"

    fills2, skip2, _ = mmrunner.plan_orphan_exit(
        state=_state("DOGE", 0.0), market_row=_row(), now_ts=NOW,
        taker_fee_bp=5.0)
    assert fills2 == [] and skip2 == "orphan_flat"


def test_orphan_exit_pnl_is_realized_not_invented():
    """退出盈亏必须真实落账且方向正确（不得凭空造利润）。

    六维口径：`spread_usd` = 相对**当前 mid** 的穿越成本，`price_usd` = 相对
    **开仓 mid** 的价格漂移，`fee_usd` = taker 费；三者相加 = 实现净额。
    情形：成本 100 买入的 $500 多头，mid 跌到 98 ⇒ 价差项与漂移项都必须为负。
    """
    st = _state("DOGE", 5000.0, avg_px=100.0, avg_mid=100.0)
    fills, _, _ = mmrunner.plan_orphan_exit(
        state=st, market_row=_row(mid=98.0, half_spread=0.05),
        now_ts=NOW, taker_fee_bp=5.0)
    f = fills[0]
    assert f.px == pytest.approx(97.95), "卖出吃对手价"
    assert f.spread_usd < 0, "穿越半价差 = 成本，必须计入 spread_usd"
    assert f.price_usd < 0, "mid 从 100 跌到 98 = 价格漂移亏损，计入 price_usd"
    assert f.fee_usd < 0, "taker 手续费必须计入成本"
    assert f.net_usd == pytest.approx(f.spread_usd + f.price_usd + f.fee_usd)
    assert f.net_usd < 0, "平掉逆势仓位必须是净亏损（不得凭空造正收益）"


# ── ④ status 可见性 ────────────────────────────────────────────────────────

def test_status_exposes_orphan_inventory():
    """status 必须暴露孤儿持仓（前端/巡检数据源），正常时为空 dict。"""
    r = _runner()
    assert r.status()["orphan_inventory"] == {}
    r.states["DOGE"] = _state("DOGE", 5000.0)
    assert r.status()["orphan_inventory"] == {"DOGE": pytest.approx(5000.0)}
