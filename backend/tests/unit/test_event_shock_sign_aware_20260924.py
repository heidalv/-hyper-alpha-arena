# -*- coding: utf-8 -*-
"""[2026-09-24 新目标 R2] `thesis_watch_reason` 的 `event_shock` **方向感知**单测。

修复背景（实测现场，logs/backend.log）：
    `[MidLongBrain] skip open BTC mid reason=watch:event_shock dir=long`
原口径只判 `|strength| >= 6`、**不看方向** ⇒ 一个"看多"的事件冲击会把**做多**开仓挡掉。
新口径：只有**与拟开方向相反**的强冲击才阻断；同向与中性都不阻断。
回滚开关 `MIDLONG_BRAIN_EVENT_SHOCK_SIGN_AWARE=false` 恢复旧行为。
"""
from __future__ import annotations

import datetime as dt

import pytest

from backend.services.mlto import brain as B
from backend.services.mlto.brain import ThesisDTO, thesis_watch_reason


def _dto(direction: str) -> ThesisDTO:
    """构造一个**新鲜**的论题（其它字段留空，避免触发别的 watch 分支）。"""
    now = dt.datetime.now(dt.timezone.utc)
    d = ThesisDTO(
        thesis_id="t-test",
        session_id="s-test",
        symbol="BTC",
        tier="mid",
        direction=direction,
    )
    for k, v in (
        ("analysis_run_id", "run-test"),
        ("expires_at", now + dt.timedelta(hours=6)),
        ("updated_at", now),
    ):
        try:
            setattr(d, k, v)
        except Exception:
            pass
    return d


@pytest.fixture(autouse=True)
def _isolate_trust_bridge(monkeypatch):
    """本文件只测**方向感知**逻辑，故把信号源信任桥关掉做隔离
    （真实复盘已把 dual:event_impact 判为 disable，会整段跳过 event_shock，
     使"反向应阻断"的断言失效——那是信任桥的正确行为，不属本文件范围）。"""
    monkeypatch.setenv("SIGNAL_SOURCE_TRUST_ENABLED", "false")
    from backend.services.analysis import source_trust as _ST
    _ST._reset_cache_for_test()
    yield
    _ST._reset_cache_for_test()


@pytest.fixture()
def _patch_ledger(monkeypatch):
    def _mk(rows):
        import backend.services.analysis.ledgers as L
        monkeypatch.setattr(L, "list_signals", lambda **kw: rows, raising=False)
        return rows
    return _mk


def _row(direction: int, strength: float, age_min: int = 1) -> dict:
    now_ms = int(dt.datetime.now(dt.timezone.utc).timestamp() * 1000)
    return {"created_ms": now_ms + age_min * 60_000, "direction": direction,
            "strength": strength, "source": "dual:event_impact"}


def test_long_shock_does_not_block_long(monkeypatch, _patch_ledger):
    """看多的强冲击（dir=+1, strength=+7）**不应**阻断做多。"""
    monkeypatch.setenv("MIDLONG_BRAIN_EVENT_SHOCK_SIGN_AWARE", "true")
    _patch_ledger([_row(+1, 7.0)])
    assert thesis_watch_reason(_dto("long"), "BTC", "mid", {}) != "event_shock"


def test_short_shock_blocks_long(monkeypatch, _patch_ledger):
    """看空的强冲击（dir=-1）应阻断做多。"""
    monkeypatch.setenv("MIDLONG_BRAIN_EVENT_SHOCK_SIGN_AWARE", "true")
    _patch_ledger([_row(-1, 7.0)])
    assert thesis_watch_reason(_dto("long"), "BTC", "mid", {}) == "event_shock"


def test_neutral_shock_does_not_block(monkeypatch, _patch_ledger):
    """中性冲击（direction=0）不构成阻断。"""
    monkeypatch.setenv("MIDLONG_BRAIN_EVENT_SHOCK_SIGN_AWARE", "true")
    _patch_ledger([_row(0, 7.0)])
    assert thesis_watch_reason(_dto("long"), "BTC", "mid", {}) != "event_shock"


def test_weak_shock_never_blocks(monkeypatch, _patch_ledger):
    """强度 < 6 一律不阻断（与旧口径一致）。"""
    monkeypatch.setenv("MIDLONG_BRAIN_EVENT_SHOCK_SIGN_AWARE", "true")
    _patch_ledger([_row(-1, 5.9)])
    assert thesis_watch_reason(_dto("long"), "BTC", "mid", {}) != "event_shock"


def test_rollback_switch_restores_sign_blind_block(monkeypatch, _patch_ledger):
    """回滚：SIGN_AWARE=false 时，同向强冲击**照样**阻断（旧行为）。"""
    monkeypatch.setenv("MIDLONG_BRAIN_EVENT_SHOCK_SIGN_AWARE", "false")
    _patch_ledger([_row(+1, 7.0)])
    assert thesis_watch_reason(_dto("long"), "BTC", "mid", {}) == "event_shock"


def test_missing_direction_field_treated_neutral(monkeypatch, _patch_ledger):
    """账本行缺 direction 字段时按中性处理（不阻断），避免 fail-closed 误拦。"""
    monkeypatch.setenv("MIDLONG_BRAIN_EVENT_SHOCK_SIGN_AWARE", "true")
    now_ms = int(dt.datetime.now(dt.timezone.utc).timestamp() * 1000)
    _patch_ledger([{"created_ms": now_ms + 60_000, "strength": 9.0}])
    assert thesis_watch_reason(_dto("long"), "BTC", "mid", {}) != "event_shock"
