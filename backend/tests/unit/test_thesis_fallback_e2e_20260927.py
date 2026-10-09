# -*- coding: utf-8 -*-
"""[新目标 R4 ④] 端到端路径锁：**长线车道的真实元数据形态**下，MLTO 学习块必须恰好进入一次。

为什么需要这条（而不是只测 helper）：`_resolve_fallback_thesis` 只证明"能解析出 thesis_id"，
但生产上真正决定"这笔交易学没学到"的是 `process_outcome` 里那段 MLTO 块：
`mlto_record`（同步写 postmortem + OWM bump）与 `learning_bus.enqueue_thesis_postmortem`（异步）
**各自只允许被调用一次**，且 `meta["session_id"]` 必须被补上（否则 OWM 权重更新落到空会话桶）。

元数据形态取自生产实测（§100.1）：长线车道 `exit_state_json.open_metadata` 只有
`e1 / entry_source / structural_stop_price`，既无 thesis_id 也无 session_id。
"""
from __future__ import annotations

import logging
from types import SimpleNamespace

import pytest

import backend.services.learning_bus as LB
import backend.services.mlto.learning_bridge as LBR
import backend.services.unified_learning_service as U
from backend.services.mlto import thesis_store as TS


@pytest.fixture
def long_lane_outcome():
    """复刻生产长线平仓的 metadata 形态（仅这三键，别无其它）。"""
    return U.TradeOutcome(
        source="paper",
        strategy_id="trend_e1:BTC",
        symbol="BTC",
        side="long",
        tier="trend_follow",          # 长线车道的 nature 化 tier
        entry_price=84558.86073,
        exit_price=83000.0,
        pnl=-15.5,
        pnl_pct=-0.018,
        duration_seconds=6 * 3600,
        metadata={
            "e1": {"note": "structural"},
            "entry_source": "trend_e1",
            "structural_stop_price": 81000.0,
            "timeframe_tier": "long",       # engine 侧无条件写入的那个字段
            "close_reason": "sl",
            "paper_position_id": 99999,
        },
    )


@pytest.fixture
def wired(monkeypatch):
    """把 process_outcome 里所有会碰 DB 的钩子换成计数器/空实现。"""
    calls = {"record": 0, "bus": 0, "meta_seen": None}

    for name in ("_update_regime_score", "_update_strategy_memory", "_track_loss_streak",
                 "_check_adaptation_needed", "_check_divergence",
                 "_check_prompt_evolution_trigger", "_evaluate_wisdom_effectiveness"):
        monkeypatch.setattr(U.unified_learning, name, lambda *a, **k: None, raising=False)
    monkeypatch.setattr(U.unified_learning, "_is_learning_enabled", lambda *a, **k: True, raising=False)
    monkeypatch.setattr(U.unified_learning, "_persist_strategy_trade", lambda *a, **k: True, raising=False)

    def _record(db, outcome, analytics_db=None):
        calls["record"] += 1
        calls["meta_seen"] = dict(outcome.metadata)

    monkeypatch.setattr(LBR, "record_outcome", _record)
    monkeypatch.setattr(TS, "get", lambda *a, **k: None)
    monkeypatch.setattr(TS, "find_latest", lambda sym, tier, **k: SimpleNamespace(
        thesis_id="25e9c215-6a81-47dc-b275-381825a23682", session_id="fa_7e12e7a1b6"))

    class _Bus:
        def enqueue_thesis_postmortem(self, outcome):
            calls["bus"] += 1
            return True

    monkeypatch.setattr(LB, "get_learning_bus", lambda: _Bus())
    # postmortem 审查官钩子（内部会开 DB）直接短路
    import backend.services.mlto.trade_review_officer as TRO
    monkeypatch.setattr(TRO, "review_close", lambda *a, **k: None, raising=False)
    return calls


def test_long_lane_close_enters_mlto_block_exactly_once(long_lane_outcome, wired, caplog):
    db = SimpleNamespace(commit=lambda: None, rollback=lambda: None)
    with caplog.at_level(logging.INFO):
        U.unified_learning.process_outcome(db, long_lane_outcome)

    # ① 兜底解析命中，且路径是跨会话（长线车道没有 session_id）
    assert "thesis_id 兜底解析" in caplog.text
    assert "via=cross_session" in caplog.text
    # ② 恰好进入一次：同步写 + 异步入队各一次（不得双写）
    assert wired["record"] == 1, f"mlto_record 调用 {wired['record']} 次（应为 1，防双写）"
    assert wired["bus"] == 1, f"bus 入队 {wired['bus']} 次（应为 1，防双写）"
    # ③ thesis_id 与 session_id 都被补上（后者决定 OWM 权重落到哪个会话）
    seen = wired["meta_seen"]
    assert seen["thesis_id"] == "25e9c215-6a81-47dc-b275-381825a23682"
    assert seen["session_id"] == "fa_7e12e7a1b6"
    # ④ 不得出现"因缺 thesis_id 未进入"的埋点
    assert "mlto_block_skip=no_thesis" not in caplog.text


def test_no_thesis_still_skips_with_trace(monkeypatch, long_lane_outcome, wired, caplog):
    """解析不到时必须走 skip 分支并留痕（覆盖率分子不会虚增）。"""
    monkeypatch.setattr(TS, "find_latest", lambda *a, **k: None)
    db = SimpleNamespace(commit=lambda: None, rollback=lambda: None)
    with caplog.at_level(logging.INFO):
        U.unified_learning.process_outcome(db, long_lane_outcome)
    assert wired["record"] == 0 and wired["bus"] == 0
    assert "mlto_block_skip=no_thesis" in caplog.text


def test_switch_off_keeps_old_behaviour(monkeypatch, long_lane_outcome, wired, caplog):
    """回滚开关（MLTO_LEARNING_THESIS_FALLBACK=false）⇒ 长线车道回到"不学习"的旧行为。"""
    monkeypatch.setenv("MLTO_LEARNING_THESIS_FALLBACK", "false")
    db = SimpleNamespace(commit=lambda: None, rollback=lambda: None)
    with caplog.at_level(logging.INFO):
        U.unified_learning.process_outcome(db, long_lane_outcome)
    assert wired["record"] == 0 and wired["bus"] == 0
    assert "thesis_id 兜底解析" not in caplog.text
