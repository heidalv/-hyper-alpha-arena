# -*- coding: utf-8 -*-
"""[P12 / P13 执行 2026-09-10] 组合闸口径切换 + 每标的并发上限。

**P12（口径切换）**：闸的输入从"幻影仓位"换到与 `PositionConstruction` 同口径的
`estimate_open_notional_aligned(equity, sl_pct, risk_pct, tranche_mult)`，由
`MIDLONG_PORTFOLIO_NOTIONAL_ALIGNED` 控制（默认 **true**，设 false 一键回滚）。
依据：§60.1 实测旧口径 $7,050（150% 权益）vs 真实 $783（16.7%），9× 倍差。

**P13（每标的并发上限）**：`MIDLONG_MAX_SAME_SYMBOL_POSITIONS`（默认 **2**，0=关闭）。
依据：§61 实测同标的并发组均值 -2.39%/胜率 27.8% vs 单笔 +3.57%/35.9%，
bootstrap 均值差 -5.96%（95%CI [-10.83%, -1.51%]，留一法仍显著）。

本测试锁定：口径开关的取值语义、闸输入确实是同口径值、每标的闸的同向计数与
零语义（0=关闭）、以及"对冲仓不计数"这一细节。
"""
from __future__ import annotations

import logging
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT))

from backend.services.mlto.midlong_portfolio_risk import (  # noqa: E402
    check_portfolio_open_allowed,
    estimate_open_notional,
    estimate_open_notional_aligned,
)

EQUITY = 4700.0
MID_SL = 0.045
MID_RISK = 0.0075


def _pos(symbol: str, side: str, notional: float, nature: str = "swing", tier: str = "mid"):
    return {
        "symbol": symbol, "side": side, "trade_nature": nature, "timeframe_tier": tier,
        "size": notional, "entry_price": 1.0, "mark_price": 1.0,
    }


def _pf(notional: float):
    """构造一个只含单笔 mid 持仓的组合快照。"""
    return {
        "balance": {"total_equity": EQUITY},
        "positions": [_pos("ETH", "long", notional)],
    }


# ── 口径（P12 的物理含义）──
def test_aligned_notional_matches_risk_budget():
    aligned = estimate_open_notional_aligned(equity=EQUITY, sl_pct=MID_SL, risk_pct=MID_RISK)
    assert aligned == pytest.approx(EQUITY * MID_RISK / MID_SL, rel=1e-9)
    legacy = estimate_open_notional(equity=EQUITY, margin_frac=0.15, leverage=10.0)
    assert legacy > aligned * 5, "旧口径仍应显著大于同口径（本测试的意义所在）"


def test_gate_input_switch_semantics(monkeypatch):
    """闸的输入换成同口径后，同一个 $783 建仓不再被 $7,050 的幻影仓位拒掉。"""
    legacy = estimate_open_notional(equity=EQUITY, margin_frac=0.30, leverage=10.0)
    aligned = estimate_open_notional_aligned(equity=EQUITY, sl_pct=MID_SL, risk_pct=MID_RISK)

    long_ok, long_why = check_portfolio_open_allowed(
        symbol="SOL", action="buy", portfolio=_pf(0.0), new_notional=legacy,
        max_net_pct=1.0,
    )
    align_ok, align_why = check_portfolio_open_allowed(
        symbol="SOL", action="buy", portfolio=_pf(0.0), new_notional=aligned,
        max_net_pct=1.0,
    )
    assert long_ok is False and "net_exposure" in long_why, (long_ok, long_why)
    assert align_ok is True, f"同口径名义应放行: {align_why}"


def test_cfg_bool_env_reports_unrecognized(monkeypatch, caplog):
    from backend.services.full_auto.midlong_helpers import _cfg_bool_env

    monkeypatch.setenv("MIDLONG_PORTFOLIO_NOTIONAL_ALIGNED", "ture")  # 拼错
    with caplog.at_level(logging.WARNING):
        assert _cfg_bool_env("MIDLONG_PORTFOLIO_NOTIONAL_ALIGNED", True) is True
    assert any("无法识别" in r.getMessage() for r in caplog.records), "拼错值被静默接受"

    monkeypatch.setenv("MIDLONG_PORTFOLIO_NOTIONAL_ALIGNED", "0")
    assert _cfg_bool_env("MIDLONG_PORTFOLIO_NOTIONAL_ALIGNED", True) is False
    monkeypatch.setenv("MIDLONG_PORTFOLIO_NOTIONAL_ALIGNED", "")
    assert _cfg_bool_env("MIDLONG_PORTFOLIO_NOTIONAL_ALIGNED", True) is True, \
        "空串/未设必须取默认（初版把空串当 False ⇒ 默认开启的开关被静默关掉）"
    monkeypatch.delenv("MIDLONG_PORTFOLIO_NOTIONAL_ALIGNED", raising=False)
    assert _cfg_bool_env("MIDLONG_PORTFOLIO_NOTIONAL_ALIGNED", True) is True


# ── 每标的并发上限（P13）──
def _set_cap(monkeypatch, value):
    """配置走 `settings`（而不是实时 env）——与生产读取路径一致。"""
    from backend.config import settings

    monkeypatch.setattr(settings, "MIDLONG_MAX_SAME_SYMBOL_POSITIONS", value, raising=False)


def test_same_symbol_cap_blocks_third_same_direction(monkeypatch):
    _set_cap(monkeypatch, 2)
    pf = {
        "balance": {"total_equity": EQUITY},
        "positions": [
            _pos("VIRTUAL", "long", 1000.0), _pos("VIRTUAL", "long", 1000.0),
            _pos("SOL", "long", 1000.0),
        ],
    }
    ok, why = check_portfolio_open_allowed(
        symbol="VIRTUAL", action="buy", portfolio=pf, new_notional=100.0, max_net_pct=50.0,
    )
    assert ok is False and "same_symbol_concurrency" in why, (ok, why)
    # 其它标的与反向（对冲）不受该闸影响
    ok2, _ = check_portfolio_open_allowed(
        symbol="SOL", action="buy", portfolio=pf, new_notional=100.0, max_net_pct=50.0,
    )
    assert ok2 is True
    ok3, why3 = check_portfolio_open_allowed(
        symbol="VIRTUAL", action="sell", portfolio=pf, new_notional=100.0, max_net_pct=50.0,
    )
    assert ok3 is True, f"反向（对冲）不应被同向并发闸拦: {why3}"


def test_same_symbol_cap_zero_disables(monkeypatch):
    """零语义：0 = 关闭该闸（不得被默认值 2 顶掉）。"""
    _set_cap(monkeypatch, 0)
    pf = {
        "balance": {"total_equity": EQUITY},
        "positions": [_pos("VIRTUAL", "long", 500.0), _pos("VIRTUAL", "long", 500.0)],
    }
    ok, why = check_portfolio_open_allowed(
        symbol="VIRTUAL", action="buy", portfolio=pf, new_notional=100.0, max_net_pct=50.0,
    )
    assert ok is True, f"0 应表示关闭，实际被拦: {why}"


def test_same_symbol_cap_default_is_two(monkeypatch):
    from backend.config import settings

    monkeypatch.delattr(settings, "MIDLONG_MAX_SAME_SYMBOL_POSITIONS", raising=False)
    pf = {
        "balance": {"total_equity": EQUITY},
        "positions": [_pos("VIRTUAL", "long", 500.0), _pos("VIRTUAL", "long", 500.0)],
    }
    ok, why = check_portfolio_open_allowed(
        symbol="VIRTUAL", action="buy", portfolio=pf, new_notional=100.0, max_net_pct=50.0,
    )
    assert ok is False and "same_symbol_concurrency" in why, (ok, why)


def test_same_symbol_cap_key_declared_for_env_override():
    """.env 能覆盖的前提：settings 必须声明该键（否则运营侧改 .env 无效——静默失效）。"""
    from backend.config import settings

    assert hasattr(settings, "MIDLONG_MAX_SAME_SYMBOL_POSITIONS"), \
        "settings 未声明 MIDLONG_MAX_SAME_SYMBOL_POSITIONS ⇒ .env 改写不会生效"
    assert int(getattr(settings, "MIDLONG_MAX_SAME_SYMBOL_POSITIONS")) >= 0
