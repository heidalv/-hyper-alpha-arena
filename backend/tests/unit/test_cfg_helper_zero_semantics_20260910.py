# -*- coding: utf-8 -*-
"""[2026-09-10 §55] 配置读取助手的「0 语义」契约测试（§24 #12 的收口）。

背景：全仓有 71 份 `_cfg_*` / `_env_*` 助手，其中一批写成
`int(getattr(settings, name, default) or default)`——**显式 0/False 会被吞成默认值**。
危险方向很关键：把闸门设成 0 通常是「最严」（例如"同簇一个都不许开"、`MAX_OPEN_POSITIONS=0`
表示关闭该闸），而 `or default` 会把它**静默放宽**成默认值 ⇒ 配置越严、闸越松。

本测试锁住：
  1. `midlong_portfolio_risk._cfg_int` 保留显式 0（§55 本轮修复）；
  2. 语义级证据：`MIDLONG_CORR_CLUSTER_MAX=0` ⇒ 同簇**第一笔就被拦**（而不是按默认 2 放行两笔）；
  3. 该模块的 int/float 助手都不再含 `or default` 形状（源码护栏）；
  4. `MIDLONG_MAX_OPEN_POSITIONS=0` 的既有语义（0=关闭该闸）未被改坏。
"""
from __future__ import annotations

import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT))

import pytest  # noqa: E402

MOD = ROOT / "backend/services/mlto/midlong_portfolio_risk.py"


def _pos(sym, tier="mid", nature="swing", side="long", size=1.0, px=100.0):
    return {"symbol": sym, "side": side, "size": size, "entry_price": px,
            "mark_price": px, "trade_nature": nature, "timeframe_tier": tier}


def _portfolio(positions, equity=100000.0):
    return {"balance": {"total_equity": equity}, "positions": positions}


def _setup(monkeypatch, *, corr_symbols="BTC,ETH,SOL", corr_max=None, max_pos=None):
    from backend.config import settings
    monkeypatch.setattr(settings, "MIDLONG_PORTFOLIO_GATE_ENABLED", True, raising=False)
    monkeypatch.setattr(settings, "MIDLONG_CORR_CLUSTER_SYMBOLS", corr_symbols, raising=False)
    monkeypatch.setattr(settings, "MIDLONG_MAX_NET_EXPOSURE_PCT", 5.0, raising=False)
    if corr_max is not None:
        monkeypatch.setattr(settings, "MIDLONG_CORR_CLUSTER_MAX", corr_max, raising=False)
    if max_pos is not None:
        monkeypatch.setattr(settings, "MIDLONG_MAX_OPEN_POSITIONS", max_pos, raising=False)


# ── ① 助手语义 ──

def test_cfg_int_keeps_explicit_zero(monkeypatch):
    from backend.config import settings
    from backend.services.mlto import midlong_portfolio_risk as m

    monkeypatch.setattr(settings, "ZZZ_INT_TEST", 0, raising=False)
    assert m._cfg_int("ZZZ_INT_TEST", 2) == 0, "显式 0 被吞成默认值（or default 复活）"
    assert m._cfg_int_allow_zero("ZZZ_INT_TEST", 2) == 0
    monkeypatch.setattr(settings, "ZZZ_INT_TEST", None, raising=False)
    assert m._cfg_int("ZZZ_INT_TEST", 2) == 2, "None 应回落默认"
    monkeypatch.setattr(settings, "ZZZ_INT_TEST", 7, raising=False)
    assert m._cfg_int("ZZZ_INT_TEST", 2) == 7


def test_cfg_int_missing_attr_falls_back(monkeypatch):
    from backend.services.mlto import midlong_portfolio_risk as m
    assert m._cfg_int("THIS_KEY_DOES_NOT_EXIST_ANYWHERE", 3) == 3


def test_module_int_float_helpers_have_no_or_default_shape():
    """源码护栏：本模块的 int/float 助手不得再出现 `getattr(...) or ...`（会吞掉显式 0）。

    用 AST 精确判定（只看代码，不看 docstring/注释）。
    """
    import ast

    tree = ast.parse(MOD.read_text(encoding="utf-8"))
    bad = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.FunctionDef) or not node.name.startswith(("_cfg_int", "_cfg_float")):
            continue
        for sub in ast.walk(node):
            if isinstance(sub, ast.BoolOp) and isinstance(sub.op, ast.Or):
                if any(isinstance(x, ast.Call) and getattr(x.func, "id", "") == "getattr"
                       for x in ast.walk(sub)):
                    bad.append(node.name)
                    break
    assert not bad, f"以下助手仍会吞掉显式 0: {sorted(set(bad))}"


# ── ② 语义级：同簇上限 = 0 ⇒ 第一笔就拦 ──

def test_corr_cluster_cap_zero_blocks_first_cluster_position(monkeypatch):
    """`MIDLONG_CORR_CLUSTER_MAX=0`（最严）必须真的"一笔同簇都不许开"。"""
    _setup(monkeypatch, corr_symbols="BTC,ETH,SOL", corr_max=0, max_pos=4)
    from backend.services.mlto.midlong_portfolio_risk import check_portfolio_open_allowed

    ok, why = check_portfolio_open_allowed(
        symbol="BTC", action="buy", portfolio=_portfolio([]), new_notional=1000.0,
    )
    assert not ok, "cap=0 时同簇第一笔就应被拦（若放行说明 0 被吞成默认 2）"
    assert "corr_cluster" in why, why


def test_corr_cluster_cap_default_allows_first_two_blocks_third(monkeypatch):
    """对照：默认 2 ⇒ 允许第 1/2 笔同簇同向、拦第 3 笔（证明 cap=0 那条不是"全拦"造成的假绿）。"""
    _setup(monkeypatch, corr_symbols="BTC,ETH,SOL", corr_max=2, max_pos=9)
    from backend.services.mlto.midlong_portfolio_risk import check_portfolio_open_allowed

    ok1, why1 = check_portfolio_open_allowed(
        symbol="BTC", action="buy", portfolio=_portfolio([]), new_notional=1000.0)
    assert ok1, why1
    ok2, why2 = check_portfolio_open_allowed(
        symbol="ETH", action="buy",
        portfolio=_portfolio([_pos("BTC")]), new_notional=1000.0)
    assert ok2, why2
    ok3, why3 = check_portfolio_open_allowed(
        symbol="SOL", action="buy",
        portfolio=_portfolio([_pos("BTC"), _pos("ETH")]), new_notional=1000.0)
    assert not ok3 and "corr_cluster" in why3, why3


# ── ③ 既有语义不被改坏：并发上限 0 = 关闭该闸 ──

def test_max_open_positions_zero_still_disables_cap(monkeypatch):
    _setup(monkeypatch, corr_symbols="", corr_max=2, max_pos=0)
    from backend.services.mlto.midlong_portfolio_risk import check_portfolio_open_allowed

    positions = [_pos(f"S{i}") for i in range(8)]
    ok, why = check_portfolio_open_allowed(
        symbol="NEW", action="buy", portfolio=_portfolio(positions), new_notional=100.0,
    )
    assert ok, f"MIDLONG_MAX_OPEN_POSITIONS=0 约定为『关闭该闸』，应放行: {why}"
