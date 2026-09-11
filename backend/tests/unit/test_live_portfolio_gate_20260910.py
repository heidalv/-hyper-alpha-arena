# -*- coding: utf-8 -*-
"""[2026-09-10 第 9 轮审计] live 模式组合闸可达性契约测试（§45.3 高危 #3）。

背景：`midlong_helpers` 的 live 分支此前只取权益、**不取持仓** ⇒ `_portfolio=None` ⇒
`collect_midlong_positions(None, None)` 返回 `[]` ⇒ **净敞口闸与并发上限在实盘完全不生效**。
实测：7 个 `trading_mode='live'` 账户在 `paper_positions` 里 **0 行**（实盘持仓只在交易所），
因此必须经 `live_executor.get_positions()` 注入。

本测试锁：
  1. 源码护栏：live 分支必须构造 `_portfolio`（含 positions）；
  2. 语义护栏：`collect_midlong_positions` 在 `portfolio=None` 时返回空（说明为何必须注入）；
  3. 注入后组合闸**真的会拦**（用 live 形状的持仓字典跑真实闸函数）。
"""
from __future__ import annotations

import os
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT))


def test_live_branch_injects_positions_into_portfolio():
    src = (ROOT / "backend" / "services" / "full_auto" / "midlong_helpers.py").read_text(
        encoding="utf-8")
    m = re.search(r"if _acct_pf and _is_live_mid:", src)
    assert m, "找不到 live 分支"
    # 截取到同级的 elif 分支为止（比固定字符窗口稳健）
    rest = src[m.end():]
    nxt = rest.find("elif _acct_pf:")
    seg = rest[: nxt] if nxt > 0 else rest[:2600]
    assert len(seg) > 400, "live 分支区间异常"
    assert "get_executor(" in seg and "get_positions(" in seg, (
        "live 分支未取实盘持仓 → 组合闸在实盘失效（§45.3 #3）"
    )
    assert '"positions"' in seg, "live 分支未构造 _portfolio['positions']"
    # 交易所持仓不含 tier/nature，必须补齐，否则会被 _is_midlong_pos 全部过滤（闸仍失效）
    assert "setdefault(\"timeframe_tier\"" in seg, (
        "live 持仓未补 timeframe_tier → _is_midlong_pos 会把它全部过滤掉，闸仍不生效"
    )
    # 失败时必须可见（不得是 debug）：消息跨行，检查其**前文窗口**里的调用级别
    idx = seg.find("live 持仓查询失败")
    assert idx > 0
    window = seg[max(0, idx - 260): idx]
    assert "logger.warning(" in window, (
        "live 持仓查询失败必须以 warning 记录（debug 在生产不可见，等于静默无保护）"
    )
    assert "logger.debug(" not in window


def test_collect_midlong_positions_empty_when_portfolio_none():
    """说明性护栏：portfolio=None ⇒ 闸看到 0 笔持仓（这正是修复前的实盘状态）。"""
    from backend.services.mlto.midlong_portfolio_risk import collect_midlong_positions
    assert collect_midlong_positions(None, None) == []


def test_gate_blocks_when_live_shaped_positions_injected(monkeypatch):
    """注入 live 形状（交易所返回字段）的持仓后，组合闸必须按上限拦截。"""
    from backend.config import settings
    from backend.services.mlto.midlong_portfolio_risk import (
        check_portfolio_open_allowed,
        collect_midlong_positions,
    )
    monkeypatch.setattr(settings, "MIDLONG_PORTFOLIO_GATE_ENABLED", True, raising=False)
    monkeypatch.setattr(settings, "MIDLONG_MAX_OPEN_POSITIONS", 3, raising=False)
    monkeypatch.setattr(settings, "MIDLONG_CORR_CLUSTER_SYMBOLS", "", raising=False)

    live_positions = [
        {"symbol": "SOL", "side": "long", "size": 2.3, "entry_price": 103.0,
         "mark_price": 100.5, "trade_nature": "position", "timeframe_tier": "long"},
        {"symbol": "ETH", "side": "long", "size": 1.0, "entry_price": 2477.0,
         "mark_price": 2460.0, "trade_nature": "trend_follow", "timeframe_tier": "long"},
        {"symbol": "XRP", "side": "long", "size": 663.0, "entry_price": 1.42,
         "mark_price": 1.39, "trade_nature": "trend_follow", "timeframe_tier": "long"},
    ]
    assert len(collect_midlong_positions(None, live_positions)) == 3
    ok, why = check_portfolio_open_allowed(
        symbol="VIRTUAL", action="buy",
        portfolio={"balance": {"total_equity": 4710.0}, "positions": live_positions},
        new_notional=900.0,
    )
    assert not ok, f"3 笔实盘持仓 + 上限 3 时必须拦，实际放行：{why}"
    assert "midlong_open_positions" in why


def test_gate_allows_below_cap_with_live_positions(monkeypatch):
    from backend.config import settings
    from backend.services.mlto.midlong_portfolio_risk import check_portfolio_open_allowed
    monkeypatch.setattr(settings, "MIDLONG_PORTFOLIO_GATE_ENABLED", True, raising=False)
    monkeypatch.setattr(settings, "MIDLONG_MAX_OPEN_POSITIONS", 3, raising=False)
    monkeypatch.setattr(settings, "MIDLONG_CORR_CLUSTER_SYMBOLS", "", raising=False)
    live_positions = [
        {"symbol": "SOL", "side": "long", "size": 2.3, "entry_price": 103.0,
         "mark_price": 100.5, "trade_nature": "position", "timeframe_tier": "long"},
    ]
    ok, why = check_portfolio_open_allowed(
        symbol="VIRTUAL", action="buy",
        portfolio={"balance": {"total_equity": 4710.0}, "positions": live_positions},
        new_notional=900.0,
    )
    assert ok, why
