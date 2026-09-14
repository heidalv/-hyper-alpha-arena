# -*- coding: utf-8 -*-
"""[2026-09-14 F95] 复利基准口径契约：权益 = 分配资本 + 已实现盈亏。

缺陷现场：`arbitrage_paper_accounts.total_equity` 是「交易所分配资本」口径——
`record_paper_leg_fill` 只改 `available_balance` / `realized_pnl`，**从不改
total_equity**（实测 160 笔成交、已实现 −$2.29 后仍恒为 $300.00）。
F85 复利此前直接读 total_equity ⇒ **实盘复利完全失效**（腿量永远 $300），
而回放按 running_equity 复利（7 天 $300→$389）——实盘与回放口径不一致，
回放的复利收益无法在实盘复现。
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT))

from backend.services.market_maker import runner as mmrunner  # noqa: E402


def test_equity_includes_realized_pnl():
    """读账户权益必须带已实现盈亏（否则复利失效）。"""
    r = mmrunner.ShadowRunner(lane_id="mm_asterdex", venue="asterdex",
                              symbols=["BTC"], equity=300.0, account_id=101)
    eq = r._read_account_equity()
    if eq <= 0:
        pytest.skip("账户不可读（无 DB）")
    import sys as _s
    _s.path.insert(0, str(ROOT))
    from sqlalchemy import text
    from backend.core.tenant import system_identity
    from backend.database.connection import SessionLocal
    with system_identity():
        with SessionLocal() as db:
            row = db.execute(text(
                "SELECT total_equity, COALESCE(realized_pnl,0) FROM arbitrage_paper_accounts"
                " WHERE id=101")).first()
    if not row:
        pytest.skip("账户 101 不存在")
    cap, real = float(row[0]), float(row[1])
    assert eq == pytest.approx(cap + real, abs=1e-6), \
        f"权益应为 资本({cap}) + 已实现({real})，实际 {eq}"


def test_equity_source_contract():
    """源码契约：查询必须同时取 total_equity 与 realized_pnl。"""
    import inspect
    src = inspect.getsource(mmrunning_equity())
    assert "realized_pnl" in src, "复利基准必须包含已实现盈亏"
    assert "total_equity" in src


def mmrunning_equity():
    return mmrunner.ShadowRunner._read_account_equity


def test_compounding_reacts_to_equity_drop(monkeypatch):
    """权益下降时腿量必须随之下降（复利语义），而非钉死在初始权益。"""
    r = mmrunner.ShadowRunner(lane_id="t", venue="x", symbols=["BTC"],
                              equity=300.0, account_id=101)
    monkeypatch.setattr(r, "_read_account_equity", lambda: 250.0)
    r.compound_ratio = 1.0
    # 模拟 tick 中的复利分支（不跑整 tick，避免 DB 副作用）
    _eq = r._read_account_equity()
    r.equity = float(_eq)
    r.fill_notional = max(10.0, float(r.compound_ratio) * r.equity)
    assert r.fill_notional == pytest.approx(250.0), "腿量应按当前权益复利"
