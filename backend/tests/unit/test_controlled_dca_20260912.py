# -*- coding: utf-8 -*-
"""[2026-09-12 F40] 受控逆势补仓前置闸契约。

现场（用户实测反馈）：行情未反转时小亏全平不合理；反事实 55-61% 收复。
F40 语义：论题同向有效 + 反转价格闸判定噪音区 + 同向失效价未破 → 才允许
evaluate_dca 全门控补仓；任一前置不满足 → skip（不平仓也不补仓）。
"""
from __future__ import annotations

import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT))

from backend.services.full_auto import midlong_position_manager as mpm  # noqa: E402
from backend.services.mlto import thesis_store as _ts  # noqa: E402
from backend.services.mlto import brain as _brain  # noqa: E402


def _th(direction="long", inv_price=1.30, should_close=False):
    inv = {"price": inv_price, "condition": "x"} if inv_price else {}
    return SimpleNamespace(direction=direction, invalidation=inv,
                           should_close=should_close, thesis_id="t1")


def _pos(mark=1.36, side="long", tier="mid"):
    return {"symbol": "XRP", "side": side, "mark_price": mark,
            "timeframe_tier": tier, "trade_nature": "swing"}


@pytest.fixture(autouse=True)
def _stubs(monkeypatch):
    monkeypatch.setattr(_ts, "get", lambda sid, sym, tier: _th())
    monkeypatch.setattr(_brain, "thesis_is_tradeable_fresh", lambda t: True)
    monkeypatch.setattr(_brain, "_inv_price", lambda inv: (inv or {}).get("price"))
    monkeypatch.setattr(mpm, "trend_broken_price_gate",
                        lambda position, side, tier: (False, "噪音区"))
    monkeypatch.setattr(mpm, "_exec_controlled_dca", lambda *a, **k: True)


def _call(mark=1.36, **kw):
    return mpm._dim_controlled_dca(
        None, account_id=14, position=_pos(mark), host=None, session=None,
    )


def test_thesis_dir_mismatch_skips(monkeypatch):
    monkeypatch.setattr(_ts, "get", lambda sid, sym, tier: _th(direction="short"))
    out = _call()
    assert out["action"] == "skip" and out["channel"] == "thesis_dir_mismatch", out


def test_should_close_pending_skips(monkeypatch):
    monkeypatch.setattr(_ts, "get", lambda sid, sym, tier: _th(should_close=True))
    out = _call()
    assert out["action"] == "skip" and out["channel"] == "should_close_pending", out


def test_trend_broken_gate_open_side_skips(monkeypatch):
    """反转价格闸放行平仓侧（亏损深/下行 regime）→ 禁止补仓。"""
    monkeypatch.setattr(mpm, "trend_broken_price_gate",
                        lambda position, side, tier: (True, "浮亏 3.5% ≥ 3.0%"))
    out = _call()
    assert out["action"] == "skip" and out["channel"] == "trend_broken_gate", out


def test_inv_breach_skips(monkeypatch):
    monkeypatch.setattr(_ts, "get", lambda sid, sym, tier: _th(inv_price=1.40))
    out = _call(mark=1.39)
    assert out["action"] == "skip" and out["channel"] == "inv_breached", out


def test_all_clear_executes_dca():
    """前置全过 → 执行补仓（stub 成交）→ dca_executed。"""
    out = _call(mark=1.36)
    assert out["action"] == "dca_executed" and out["channel"] == "dca", out


def test_manage_flow_wired():
    """模式锁：manage_position 必须接入 F40 维度与开关。"""
    import inspect
    src = inspect.getsource(mpm.manage_position)
    assert "_dim_controlled_dca" in src
    assert "MIDLONG_CONTROLLED_DCA_ENABLED" in src
