# -*- coding: utf-8 -*-
"""[§88 契约 2026-09-11 / 决策 P29-A+B] long 层止损距离上限 + 风险预算对齐。

决策（用户 P29-C）：A 把 long SL 拉近到 2.5–3% + B 风险预算 1.25% → 0.75%。
本文件锁三件事：
  1. `clamp_sl_price()` 的**行为**（过远拉近 / 过近不动 / 上限 0 = 关闭 / 多空对称）；
  2. **实际生效值**（`.env` + 环境变量）：`MIDLONG_SL_MAX_PCT_LONG=0.03`、
     `PC_RISK_PER_TRADE_PCT_LONG=0.0075`（只断言"当前部署的决策值"，不是默认值）；
  3. **接线**：夹子必须在下单收口点被调用（否则等于没改）。
"""
from __future__ import annotations

import inspect
import os

import pytest

import backend.services.paper_trading_engine as pte

P = pte.PaperTradingEngine


def test_long_far_sl_is_pulled_in(monkeypatch):
    """long 的 6.5% SL（§87 实测形态）⇒ 被拉近到 3%。"""
    monkeypatch.setenv("MIDLONG_SL_MAX_PCT_LONG", "0.03")
    sl, clamped, why = P.clamp_sl_price(935.0, side="long", entry=1000.0, tier="long")
    assert clamped is True and sl == pytest.approx(970.0) and "long_sl_cap" in why


def test_long_tight_sl_untouched(monkeypatch):
    """过近的 SL 不归它管（由 `_MIN_SL_DISTANCE_BY_NATURE` 负责），必须原样返回。"""
    monkeypatch.setenv("MIDLONG_SL_MAX_PCT_LONG", "0.03")
    sl, clamped, why = P.clamp_sl_price(990.0, side="long", entry=1000.0, tier="long")
    assert clamped is False and sl == 990.0 and why == "within_cap"


def test_short_symmetric(monkeypatch):
    """空头对称：SL 高于 entry×(1+cap) ⇒ 拉回。"""
    monkeypatch.setenv("MIDLONG_SL_MAX_PCT_LONG", "0.03")
    sl, clamped, _ = P.clamp_sl_price(1065.0, side="short", entry=1000.0, tier="long")
    assert clamped is True and sl == pytest.approx(1030.0)


def test_cap_off_by_default(monkeypatch):
    """未配置（或 0）⇒ 完全不动（回滚位）。"""
    monkeypatch.delenv("MIDLONG_SL_MAX_PCT_LONG", raising=False)
    monkeypatch.delenv("MIDLONG_SL_MAX_PCT", raising=False)
    sl, clamped, why = P.clamp_sl_price(100.0, side="long", entry=1000.0, tier="long")
    assert clamped is False and sl == 100.0 and why == "off"


def test_tier_specific_then_global_fallback(monkeypatch):
    """`MIDLONG_SL_MAX_PCT_LONG` 优先；没配则回退全局 `MIDLONG_SL_MAX_PCT`。"""
    monkeypatch.delenv("MIDLONG_SL_MAX_PCT_LONG", raising=False)
    monkeypatch.setenv("MIDLONG_SL_MAX_PCT", "0.05")
    assert P.sl_max_pct_for_tier("long") == pytest.approx(0.05)
    monkeypatch.setenv("MIDLONG_SL_MAX_PCT_LONG", "0.02")
    assert P.sl_max_pct_for_tier("long") == pytest.approx(0.02)


def test_deployed_effective_values_match_p29_decision():
    """**生效值断言**：当前部署应当是 P29-C 决定的参数（0.03 / 0.0075）。

    这条用例是"决策落地"的机器凭据：改回来就会红。
    """
    from dotenv import load_dotenv
    load_dotenv(r"D:\001Alpha\Hyper-Alpha-Arena\.env", override=False)
    assert float(os.environ.get("MIDLONG_SL_MAX_PCT_LONG", "0")) == pytest.approx(0.03)
    assert float(os.environ.get("PC_RISK_PER_TRADE_PCT_LONG", "0")) == pytest.approx(0.0075)
    assert P.sl_max_pct_for_tier("long") == pytest.approx(0.03)


def test_clamp_is_wired_into_order_creation():
    """接线护栏：下单收口点必须调用 `clamp_sl_price`（只定义不接线 = 没修）。"""
    src = inspect.getsource(pte)
    idx = src.index("order = PaperOrder(")
    window = src[idx:idx + 1400]
    assert "clamp_sl_price(" in window, "PaperOrder 创建点没有夹 SL 距离 ⇒ P29-A 未生效"
    assert "order.sl_price = _sl2" in window, "夹住后没有回写 order.sl_price"
