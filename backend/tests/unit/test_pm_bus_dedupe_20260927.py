# -*- coding: utf-8 -*-
"""[新目标 R4] postmortem 事件双写/静默节流的修复单测。

背景（§100 实测，账户 14，09-15→09-27）：
  · `mlto_thesis_events` 里 146 条 postmortem 中 **34 组同 (thesis, pnl, reason) 重复**
    —— 因为两个写入方都会写：`learning_bridge.record_outcome`（同步，先跑）与
    `learning_bus.enqueue_thesis_postmortem` 的异步 worker（后跑，此前**不查重**）。
  · 节流命中是**完全静默**的 `return False` ⇒ 漏斗审计无法区分"没进 MLTO 块"与
    "被节流吃掉"。
本单测锁住修复语义：① 开关默认开、非法值 fail-closed；② 已存在则不再写；
③ 节流命中必须留日志证据；④ 回滚开关 = 退回旧行为。
"""
from __future__ import annotations

import logging

import pytest

import backend.services.learning_bus as LB
from backend.services.mlto import learning_bridge as LBR


class _Outcome:
    def __init__(self, symbol="TESTSYM", tier="mid", thesis_id="th-test-1"):
        self.symbol = symbol
        self.tier = tier
        self.pnl = 1.23
        self.pnl_pct = 0.0123
        self.exit_channel = "sl"
        self.strategy_id = "tpl_test"
        self.metadata = {
            "thesis_id": thesis_id,
            "tier": tier,
            "close_reason": "sl",
            "pnl": self.pnl,
        }


@pytest.fixture(autouse=True)
def _clean(monkeypatch):
    monkeypatch.delenv("MLTO_PM_DEDUPE_ON_BUS", raising=False)
    monkeypatch.delenv("MLTO_OWM_PER_TRADE_DEDUPE", raising=False)
    bus = LB.LearningBus()
    bus._thesis_postmortem_last.clear()
    yield
    bus._thesis_postmortem_last.clear()


@pytest.fixture
def sync_thread(monkeypatch):
    """把 worker 线程改成同步执行，让断言确定（否则要 sleep 等线程）。"""
    class _SyncThread:
        def __init__(self, target=None, daemon=None, name=None, **kw):
            self._target = target

        def start(self):
            if self._target:
                self._target()

    monkeypatch.setattr(LB.threading, "Thread", _SyncThread)


def test_switch_default_on_and_fail_closed(monkeypatch):
    assert LB._pm_dedupe_on_bus_enabled() is True
    monkeypatch.setenv("MLTO_PM_DEDUPE_ON_BUS", "false")
    assert LB._pm_dedupe_on_bus_enabled() is False
    monkeypatch.setenv("MLTO_PM_DEDUPE_ON_BUS", "garbage")
    assert LB._pm_dedupe_on_bus_enabled() is False  # 非法值 fail-closed


def test_worker_skips_when_postmortem_present(monkeypatch, sync_thread, caplog):
    """核心修复：同步方已落库 ⇒ 异步 worker 不得再写一条。

    [R4 逐笔] 默认开关下 worker 走**逐笔**查重（`_has_event_for_trade`），
    所以这里 pat 逐笔助手；`_has_postmortem`（按 thesis）只在回滚档使用。
    """
    monkeypatch.setattr(LBR, "_has_event_for_trade", lambda *a, **k: True)
    monkeypatch.setattr(LBR, "_has_postmortem", lambda *a, **k: True)
    wrote = {"n": 0}
    monkeypatch.setattr(
        "backend.services.mlto.thesis_store.append_event",
        lambda *a, **k: wrote.__setitem__("n", wrote["n"] + 1),
    )
    with caplog.at_level(logging.INFO):
        ok = LB.LearningBus().enqueue_thesis_postmortem(_Outcome())
    assert ok is True, "入队本身应成功（节流表已占位）"
    assert wrote["n"] == 0, "已有 postmortem 仍重复写（双写闸失效）"
    assert "skip=already_present" in caplog.text


def test_worker_writes_when_absent(monkeypatch, sync_thread):
    """无人写过时照旧写，行为不变。"""
    monkeypatch.setattr(LBR, "_has_event_for_trade", lambda *a, **k: False)
    monkeypatch.setattr(LBR, "_has_postmortem", lambda *a, **k: False)
    wrote = {"n": 0}
    monkeypatch.setattr(
        "backend.services.mlto.thesis_store.append_event",
        lambda *a, **k: wrote.__setitem__("n", wrote["n"] + 1),
    )
    assert LB.LearningBus().enqueue_thesis_postmortem(_Outcome()) is True
    assert wrote["n"] == 1, "无重复时应写且只写一条"


def test_rollback_switch_restores_old_behaviour(monkeypatch, sync_thread):
    """回滚开关：置 false ⇒ 退回"不查重、照写"（旧行为）。"""
    monkeypatch.setenv("MLTO_PM_DEDUPE_ON_BUS", "false")
    monkeypatch.setattr(LBR, "_has_postmortem", lambda *a, **k: True)
    wrote = {"n": 0}
    monkeypatch.setattr(
        "backend.services.mlto.thesis_store.append_event",
        lambda *a, **k: wrote.__setitem__("n", wrote["n"] + 1),
    )
    assert LB.LearningBus().enqueue_thesis_postmortem(_Outcome()) is True
    assert wrote["n"] == 1, "回滚后应恢复写（旧行为）"


def test_cooldown_skip_is_logged(monkeypatch, sync_thread, caplog):
    """节流命中必须留证据（此前是完全静默的 return False）。"""
    monkeypatch.setattr(LBR, "_has_event_for_trade", lambda *a, **k: False)
    monkeypatch.setattr(LBR, "_has_postmortem", lambda *a, **k: False)
    monkeypatch.setattr(
        "backend.services.mlto.thesis_store.append_event", lambda *a, **k: None
    )
    bus = LB.LearningBus()
    out = _Outcome(symbol="TESTSYM2")
    assert bus.enqueue_thesis_postmortem(out) is True
    with caplog.at_level(logging.INFO):
        assert bus.enqueue_thesis_postmortem(out) is False, "同 symbol+tier 在节流窗内必须被挡"
    assert "skip=cooldown" in caplog.text, "节流命中未留日志 ⇒ 漏斗不可观测"


def test_worker_thesis_level_rollback(monkeypatch, sync_thread):
    """R4 回滚档：MLTO_OWM_PER_TRADE_DEDUPE=false ⇒ worker 退回按 thesis 查重。"""
    monkeypatch.setenv("MLTO_OWM_PER_TRADE_DEDUPE", "false")
    monkeypatch.setattr(LBR, "_has_postmortem", lambda *a, **k: True)
    wrote = {"n": 0}
    monkeypatch.setattr(
        "backend.services.mlto.thesis_store.append_event",
        lambda *a, **k: wrote.__setitem__("n", wrote["n"] + 1),
    )
    assert LB.LearningBus().enqueue_thesis_postmortem(_Outcome()) is True
    assert wrote["n"] == 0, "回滚档：thesis 已有 postmortem ⇒ worker 不写（旧口径）"
