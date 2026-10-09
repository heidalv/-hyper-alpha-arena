# -*- coding: utf-8 -*-
"""[2026-09-27 R4 用户指令] 修「mid 车道一直在空转」：按需建的 AI 策略必须**落库**。

## 现场（账户 14，2026-09-27 22:03，LINK mid）
```
[AIStrat] 为 AI 候选按需建策略 LINK tier=mid sid=ai_auto_linkmid_0a5d (母本=auto_efd68a88ca, 今日第 1/8 个)
[TrancheGate] DOWNSIZE LINK tier=mid stage→size×0.01
[SizeFloor] PROBE-CLAMP LINK tier=mid … 抬到 ×0.1938（名义≈$400）
[V5Gate] PASS symbol=LINK action=buy conf=55.0 nature=swing
[FullAuto] _execute_paper_trade: 策略对象无效或已 detach (sym=LINK)
[MidLongBrain] execute_false LINK mid dir=long …      ← 整条开仓中止在最后一步
```
DB 交叉验证：`SELECT count(*) FROM ai_strategies WHERE strategy_id LIKE 'ai_auto_%'
AND created_at >= 今天` = **0** —— 建出来的策略**全部随事务蒸发**。

## 机制
`provision_ai_strategy()` 只 `db.add(row); db.flush()`，从不 `commit`；
而执行侧 `paper_execution._execute_paper_trade_inner` 用 **fresh session**
（`_FreshDB`）调 `ensure_bound_strategy` → `load_strategy_by_id` 用新会话按
`strategy_id` 重查 ⇒ 查不到未提交的行 ⇒ 返回 None ⇒ 中止。

## 本文件锁什么
1. 开关默认开、非法值 fail-closed；
2. 开关打开时**必须真的 commit**；关闭时**不得 commit**（回滚语义）；
3. commit 失败必须按"未建策略"返回 None（fail-closed，不引入放行口）。
"""
from __future__ import annotations

import logging
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

import backend.services.full_auto.midlong_helpers as MH


@pytest.fixture(autouse=True)
def _clean(monkeypatch):
    monkeypatch.delenv("MIDLONG_AISTRAT_PROVISION_COMMIT", raising=False)
    yield


def test_switch_default_on_and_fail_closed(monkeypatch):
    assert MH._aistrat_provision_commit_enabled() is True
    monkeypatch.setenv("MIDLONG_AISTRAT_PROVISION_COMMIT", "false")
    assert MH._aistrat_provision_commit_enabled() is False
    monkeypatch.setenv("MIDLONG_AISTRAT_PROVISION_COMMIT", "garbage")
    assert MH._aistrat_provision_commit_enabled() is False  # 非法值 = 旧行为


def _patch_pipeline(monkeypatch):
    monkeypatch.setattr(MH, "ai_autocreate_config",
                        lambda: {"enabled": True, "max_per_day": 8, "allow_live": False})
    monkeypatch.setattr(MH, "ai_pool_symbols", lambda db, session, tier: {"LINK"})
    monkeypatch.setattr(MH, "count_ai_provisioned_today", lambda db: 0)
    monkeypatch.setattr(MH, "pick_strategy_donor", lambda db, acct, tier: SimpleNamespace(strategy_id="donor-1"))
    row = SimpleNamespace(strategy_id="ai_auto_linkmid_test", primary_symbol="LINK")
    monkeypatch.setattr(MH, "build_cloned_strategy",
                        lambda donor, symbol=None, tier=None, account_id=None, session_id=None: row)
    return row


def test_commit_called_when_enabled(monkeypatch):
    row = _patch_pipeline(monkeypatch)
    db = MagicMock()
    session = SimpleNamespace(session_id="fa_test", trading_mode="paper", account_id=14,
                              active_strategy_ids=[])
    out = MH.provision_ai_strategy(db, session, "LINK", "mid", account_id=14,
                                   host=SimpleNamespace(append_event=lambda *a, **k: None))
    assert out is row, "应当返回新建策略"
    assert db.commit.called, "开关打开时必须落库 —— 否则执行侧 fresh session 查不到"
    assert row.strategy_id in session.active_strategy_ids


def test_no_commit_when_rolled_back(monkeypatch):
    _patch_pipeline(monkeypatch)
    monkeypatch.setenv("MIDLONG_AISTRAT_PROVISION_COMMIT", "false")
    db = MagicMock()
    session = SimpleNamespace(session_id="fa_test", trading_mode="paper", account_id=14,
                              active_strategy_ids=[])
    MH.provision_ai_strategy(db, session, "LINK", "mid", account_id=14,
                             host=SimpleNamespace(append_event=lambda *a, **k: None))
    assert not db.commit.called, "回滚开关必须退回『只 flush 不 commit』的旧行为"


def test_commit_failure_returns_none(monkeypatch, caplog):
    """落库失败 ⇒ 按未建策略返回 None（fail-closed，不得引入新的放行口）。"""
    _patch_pipeline(monkeypatch)
    db = MagicMock()
    db.commit.side_effect = RuntimeError("db down")
    session = SimpleNamespace(session_id="fa_test", trading_mode="paper", account_id=14,
                              active_strategy_ids=[])
    with caplog.at_level(logging.WARNING):
        out = MH.provision_ai_strategy(db, session, "LINK", "mid", account_id=14,
                                       host=SimpleNamespace(append_event=lambda *a, **k: None))
    assert out is None
    assert "新建策略落库失败" in caplog.text
