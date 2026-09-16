# -*- coding: utf-8 -*-
"""[调研轮37 2026-09-17] 空头来源限制（模板族不得开空）契约测试。

## 依据

30 天实测（用户指令「做空要认真做」）：
* mid 空单 n=38 净 −68.60、均 **−1.81**、胜率 **26%**（多单对照 −0.58 / 47%）；
* **`tpl_模板` 空单 27 笔均 −2.12、胜率 30%** 为最大贡献者；模板族多头侧同样最大亏损（long −127/21）；
* 空头分位曲线（位置闸拦下的 420 行样本）：低分位 12h −0.71%/胜率 20%，放行的 ≥40 分位同样亏
  ⇒ **没有可调阈值解决的正区间**，因此先掐"来源 + 规模"。

## 锁定语义

1. 做空 + 模板族策略行 ⇒ 拦（审计码 `short_template_source_block`，可见可统计）；
2. **多头不受影响**、**非模板来源的空单不受影响**（只掐已证最差的那一族）；
3. 开关关闭 ⇒ 不拦（回滚位）。
"""
from __future__ import annotations

import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

ROOT = Path(__file__).resolve().parents[3]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from backend.services.full_auto import proposal_execution as pe  # noqa: E402


def _proposal(action="sell", sym="UNI", tier="mid"):
    return SimpleNamespace(action=action, symbol=sym, tier=tier,
                           trade_nature="swing", confidence=70)


class _Host:
    def __init__(self, sid="tpl_mid_range_abc123"):
        self._sid = sid

    def midlong_persistence_allow(self, *a, **k):
        return True

    def session_trading_mode(self, session):
        return "paper"

    def resolve_independent_strategy(self, db, session, sym, tier):
        return SimpleNamespace(strategy_id=self._sid, primary_symbol=sym,
                               timeframe_tier=tier, account_id=14)


def _call(host, action="sell"):
    """调用被测函数；**后续环节**（无 db / 无真实依赖）的异常不影响断言。"""
    try:
        return pe.evaluate_and_execute_proposal(
            db=None, session=_session(), proposal=_proposal(action),
            market_summary={}, host=host, session_mode="running",
        )
    except Exception:  # noqa: BLE001
        return None


def _session():
    return SimpleNamespace(session_id="fa_test", paper_account_id=14, account_id=14,
                           trading_mode="paper")


@pytest.fixture(autouse=True)
def _capture_blocks(monkeypatch):
    seen = []
    import backend.services.mlto.open_block_reason as obr

    monkeypatch.setattr(obr, "mark_open_block",
                        lambda code, detail="", layer="": seen.append((code, detail)), raising=True)
    return seen


def test_template_short_is_blocked(_capture_blocks):
    ok = pe.evaluate_and_execute_proposal(
        db=None, session=_session(), proposal=_proposal("sell"),
        market_summary={}, host=_Host("tpl_mid_range_abc123"), session_mode="running",
    )
    assert ok is False
    assert any(c == "short_template_source_block" for c, _ in _capture_blocks), _capture_blocks


def test_template_long_is_not_blocked(_capture_blocks):
    """多头不受影响：本项只针对做空。"""
    _call(_Host("tpl_mid_range_abc123"), "buy")
    assert not any(c == "short_template_source_block" for c, _ in _capture_blocks), _capture_blocks


def test_non_template_short_is_not_blocked(_capture_blocks):
    """非模板来源的空单不受影响（保留做空机会）。"""
    _call(_Host("auto_dfa9e3b348"), "sell")
    assert not any(c == "short_template_source_block" for c, _ in _capture_blocks), _capture_blocks


def test_switch_off_is_rollback(monkeypatch, _capture_blocks):
    from backend.config import settings as s

    monkeypatch.setattr(s, "MIDLONG_SHORT_BLOCK_TEMPLATE_SOURCES", False, raising=False)
    _call(_Host("tpl_mid_range_abc123"), "sell")
    assert not any(c == "short_template_source_block" for c, _ in _capture_blocks)
