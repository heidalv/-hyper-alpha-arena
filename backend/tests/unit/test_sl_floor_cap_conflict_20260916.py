# -*- coding: utf-8 -*-
"""[调研轮15b 2026-09-16] 止损「层上限 vs nature 硬下限」冲突的契约测试。

## 背景（线上实测）

上游三层封顶（提案层 `clamp_stop_distance` / 价格层 `tp_sl_prices` /
`PositionMemoryManager._calc_tp_sl`）都改完后，线上 mid 仓硬止损距离**仍是 4.67%**，
long 仓 **6.50%** —— 因为 `paper_trading_engine` 每个保护 tick 会调用
`_enforce_min_sl()`，按 nature 硬下限把 SL **拉回** swing 4.5% / position 5.5% /
trend_follow 6.5%。实测：

    4659 BTC[long] SL距 6.50% / 下限 6.50%   ← 完全等于下限
    4685..4690[mid] SL距 4.67% / 下限 4.50%

即"止损远到不会响"（赢家 MAE 最大 1.20%，§87 审计 long 层 SL 中位 6.52% ⇒ `sl`
通道 0 笔）的唯一存活原因。此外 mid 层只配了另一种拼写
`MIDLONG_MAX_SL_PCT_MID`，引擎收口层读的 `MIDLONG_SL_MAX_PCT_MID` 缺失 ⇒ cap=0，
两种拼写不一致也让上限静默失效。

## 锁定语义

1. 上限比下限更紧 ⇒ **上限赢**（下限降为上限值）；
2. 上限未配置/为 0 ⇒ 行为与历史完全一致（回滚位）；
3. 只夹"过远"一侧，**保本/盈利侧止损永不被动**；
4. 两种拼写都能关闭/开启提案层上限（显式 0 = 关闭，不再回退另一键）。
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[3]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from backend.services import paper_trading_engine as pte  # noqa: E402
from backend.services.mlto import midlong_trade_design as mtd  # noqa: E402

P = pte.PaperTradingEngine


class _Pos:
    """最小持仓替身：只需要 _enforce_min_sl / clamp_sl_price 用到的字段。"""

    def __init__(self, *, side="long", entry=100.0, sl=99.0, tier="mid",
                 strategy_id="midlong_ai", nature="swing"):
        self.side = side
        self.entry_price = entry
        self.sl_price = sl
        self.timeframe_tier = tier
        self.strategy_id = strategy_id
        self.trade_nature = nature


def _clear_cap(monkeypatch):
    for k in ("MIDLONG_SL_MAX_PCT", "MIDLONG_SL_MAX_PCT_MID",
              "MIDLONG_SL_MAX_PCT_LONG"):
        monkeypatch.delenv(k, raising=False)


# ── 1. 上限赢：mid 层 ────────────────────────────────────────────────
def test_mid_cap_wins_over_swing_floor(monkeypatch):
    """mid：AI 给 1% 紧止损 ⇒ 抬到**上限 2%**，而不是 nature 下限 4.5%。"""
    _clear_cap(monkeypatch)
    monkeypatch.setenv("MIDLONG_SL_MAX_PCT_MID", "0.02")
    pos = _Pos(side="long", entry=100.0, sl=99.0, tier="mid", nature="swing")
    P._enforce_min_sl(pos, 100.0, "swing", "mid")
    assert pos.sl_price == pytest.approx(98.0), "上限应作为新的下限（2%）"


def test_mid_legacy_floor_when_cap_off(monkeypatch):
    """回滚位：上限未配置 ⇒ 仍是历史行为（swing 4.5%）。"""
    _clear_cap(monkeypatch)
    pos = _Pos(side="long", entry=100.0, sl=99.0, tier="mid", nature="swing")
    P._enforce_min_sl(pos, 100.0, "swing", "mid")
    assert pos.sl_price == pytest.approx(95.5), "未配置上限时必须保持 4.5% 旧行为"


def test_long_cap_wins_over_trend_follow_floor(monkeypatch):
    """long：trend_follow 下限 6.5% 被 3% 上限取代（§87 的 6.52% 形态）。"""
    _clear_cap(monkeypatch)
    monkeypatch.setenv("MIDLONG_SL_MAX_PCT_LONG", "0.03")
    pos = _Pos(side="long", entry=1000.0, sl=994.0, tier="long",
               nature="trend_follow")
    P._enforce_min_sl(pos, 1000.0, "trend_follow", "long")
    assert pos.sl_price == pytest.approx(970.0)


def test_cap_never_loosens_below_floor(monkeypatch):
    """上限比下限更宽时不生效：floor 1.2%（MR）遇到 cap 2% ⇒ 仍用 1.2%。"""
    _clear_cap(monkeypatch)
    monkeypatch.setenv("MIDLONG_SL_MAX_PCT_MID", "0.02")
    pos = _Pos(side="long", entry=100.0, sl=99.5, tier="mid",
               strategy_id="scalp_mr_x", nature="swing")
    P._enforce_min_sl(pos, 100.0, "swing", "mid")
    assert pos.sl_price == pytest.approx(98.8), "MR 专用 1.2% 下限不该被放宽到 2%"


# ── 2. 每 tick 收窄（夹"过远"一侧）────────────────────────────────────
def test_tick_clamp_narrows_legacy_far_sl(monkeypatch):
    """存量仓 4.5% 的 SL ⇒ 随 tick 收窄到 2%。"""
    _clear_cap(monkeypatch)
    monkeypatch.setenv("MIDLONG_SL_MAX_PCT_MID", "0.02")
    sl, clamped, why = P.clamp_sl_price(95.5, side="long", entry=100.0, tier="mid")
    assert clamped is True and sl == pytest.approx(98.0) and "long_sl_cap" in why


def test_tick_clamp_off_by_default(monkeypatch):
    """上限未配置 ⇒ 完全不动的回滚位。"""
    _clear_cap(monkeypatch)
    sl, clamped, why = P.clamp_sl_price(95.5, side="long", entry=100.0, tier="mid")
    assert clamped is False and sl == 95.5 and why == "off"


@pytest.mark.parametrize("side,sl", [("long", 101.0), ("short", 99.0)])
def test_tick_clamp_never_touches_profit_side(monkeypatch, side, sl):
    """保本/盈利侧止损（long: SL>entry；short: SL<entry）必须原样返回。"""
    _clear_cap(monkeypatch)
    monkeypatch.setenv("MIDLONG_SL_MAX_PCT_MID", "0.02")
    got, clamped, _ = P.clamp_sl_price(sl, side=side, entry=100.0, tier="mid")
    assert clamped is False and got == sl


def test_short_symmetric_clamp(monkeypatch):
    _clear_cap(monkeypatch)
    monkeypatch.setenv("MIDLONG_SL_MAX_PCT_MID", "0.02")
    sl, clamped, _ = P.clamp_sl_price(104.5, side="short", entry=100.0, tier="mid")
    assert clamped is True and sl == pytest.approx(102.0)


def test_short_tier_untouched(monkeypatch):
    """短线层不配该键 ⇒ 不受影响（用户已明确停用短线车道，不能被顺手改）。"""
    _clear_cap(monkeypatch)
    monkeypatch.setenv("MIDLONG_SL_MAX_PCT_MID", "0.02")
    sl, clamped, why = P.clamp_sl_price(90.0, side="long", entry=100.0, tier="short")
    assert clamped is False and sl == 90.0 and why == "off"


# ── 3. 键名拼写兼容（提案层）─────────────────────────────────────────
def test_proposal_clamp_accepts_engine_spelling(monkeypatch):
    """只配引擎拼写 `MIDLONG_SL_MAX_PCT_MID` 时，提案层也必须读到 2%。"""
    from backend.config import settings as s

    monkeypatch.setattr(s, "MIDLONG_MAX_SL_PCT_MID", None, raising=False)
    monkeypatch.setattr(s, "MIDLONG_SL_MAX_PCT_MID", 0.02, raising=False)
    cap, why = mtd.clamp_stop_distance(0.05, "mid")
    assert cap == pytest.approx(0.02) and "上限" in why


def test_proposal_clamp_explicit_zero_disables(monkeypatch):
    """显式 0 = 关闭（不再回退到另一种拼写）。"""
    from backend.config import settings as s

    monkeypatch.setattr(s, "MIDLONG_MAX_SL_PCT_MID", 0.0, raising=False)
    monkeypatch.setattr(s, "MIDLONG_SL_MAX_PCT_MID", 0.02, raising=False)
    cap, why = mtd.clamp_stop_distance(0.05, "mid")
    assert cap == pytest.approx(0.05) and why == "cap_off"


# ── 4. 部署凭据：.env 生效值（改回去就红）────────────────────────────
def test_deployed_env_keys_present():
    from dotenv import load_dotenv

    load_dotenv(str(ROOT / ".env"), override=False)
    assert float(os.environ.get("MIDLONG_SL_MAX_PCT_MID", "0")) == pytest.approx(0.02)
    assert float(os.environ.get("MIDLONG_SL_MAX_PCT_LONG", "0")) == pytest.approx(0.03)
