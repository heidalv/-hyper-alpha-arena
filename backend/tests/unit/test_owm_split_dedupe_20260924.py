# -*- coding: utf-8 -*-
"""[2026-09-24 新目标 R22] OWM 去重拆分单测。

缺陷：`record_outcome` 只要查到该 thesis 已有 postmortem 就整体 return，
把 `_bump_owm`（学习权重更新）一并跳过。而 postmortem 有两个写入方
（`learning_bus` 异步线程 + 本函数），bus 常先落库 ⇒ OWM 长期不更新。
实测：bridge 侧 postmortem 09-17~09-23 全 0，`mlto_signal_weights` 停在 09-18 12:40。

本单测锁定修复语义：postmortem 存在与否，不得阻止 OWM bump。
"""
from __future__ import annotations

from types import SimpleNamespace

import pytest

from backend.services.mlto import learning_bridge as LB


@pytest.fixture(autouse=True)
def _pin_legacy_dedupe(monkeypatch):
    """[R4 2026-09-28] 本文件锁定的是 R22 的**按 thesis 去重**语义，
    它现在是 `MLTO_OWM_PER_TRADE_DEDUPE=false` 的回滚档（逐笔去重的正/反契约
    在 `test_owm_per_trade_dedupe_20260928.py`）。钉死开关，避免默认档变化让本文件假红。
    """
    monkeypatch.setenv("MLTO_OWM_PER_TRADE_DEDUPE", "false")


def _outcome(pnl: float = 1.0):
    return SimpleNamespace(
        metadata={
            "thesis_id": "th-1",
            "close_reason": "tp",
            "timeframe_tier": "mid",
        },
        pnl=pnl,
        pnl_pct=0.01,
        strategy_id="auto_test",
        symbol="BTC",
        exit_channel="tp",
        tier="mid",
    )


def _install_fake_thesis_store(monkeypatch, seen):
    """安装 thesis_store 替身。

    注意：`record_outcome` 里是 `from backend.services.mlto import thesis_store`。
    当真实子模块已被本进程其它测试导入过时，该语句取的是**包属性**而不是
    `sys.modules` 里那条记录 —— 只 patch sys.modules 会静默失效（本单测最初
    单跑通过、并入宽子集后失败，就是这个原因）。因此两处都要替换。
    """
    import sys

    import backend.services.mlto as _pkg

    class _TS:
        @staticmethod
        def append_event(thesis_id, event_type, payload, db=None):
            if event_type == "postmortem":
                seen["pm_write"] += 1

    monkeypatch.setitem(sys.modules, "backend.services.mlto.thesis_store", _TS)
    monkeypatch.setattr(_pkg, "thesis_store", _TS, raising=False)
    return _TS


@pytest.fixture()
def spies(monkeypatch):
    seen = {"bump": 0, "mark": 0, "pm_write": 0}

    def _bump(*a, **k):
        seen["bump"] += 1
        return "sources=[llm] delta=+0.0050"

    def _mark(*a, **k):
        seen["mark"] += 1

    _install_fake_thesis_store(monkeypatch, seen)
    monkeypatch.setattr(LB, "_bump_owm", _bump)
    monkeypatch.setattr(LB, "_mark_owm_bump", _mark)
    monkeypatch.setenv("MLTO_OWM_SPLIT_DEDUPE", "true")
    return seen


def test_bumps_owm_even_when_postmortem_exists(spies, monkeypatch):
    """核心修复：bus 已写 postmortem 时，OWM 仍必须更新。"""
    monkeypatch.setattr(LB, "_has_postmortem", lambda *a, **k: True)
    monkeypatch.setattr(LB, "_has_owm_bump", lambda *a, **k: False)
    LB.record_outcome(None, _outcome(), None)
    assert spies["bump"] == 1, "postmortem 存在不得阻止 OWM bump"
    assert spies["mark"] == 1, "bump 后必须写下独立标记"
    assert spies["pm_write"] == 0, "postmortem 已存在 ⇒ 不应重复写"


def test_no_double_bump_when_marker_present(spies, monkeypatch):
    """双触发保护仍然有效：标记已在 ⇒ 不重复 bump。"""
    monkeypatch.setattr(LB, "_has_postmortem", lambda *a, **k: True)
    monkeypatch.setattr(LB, "_has_owm_bump", lambda *a, **k: True)
    LB.record_outcome(None, _outcome(), None)
    assert spies["bump"] == 0
    assert spies["mark"] == 0


def test_writes_postmortem_when_absent(spies, monkeypatch):
    """无人写过 postmortem 时照旧写，并且 bump + mark 各一次。"""
    monkeypatch.setattr(LB, "_has_postmortem", lambda *a, **k: False)
    monkeypatch.setattr(LB, "_has_owm_bump", lambda *a, **k: False)
    LB.record_outcome(None, _outcome(), None)
    assert (spies["bump"], spies["mark"], spies["pm_write"]) == (1, 1, 1)


def test_legacy_mode_restores_old_behaviour(spies, monkeypatch):
    """回滚开关：MLTO_OWM_SPLIT_DEDUPE=false ⇒ 退回"有 postmortem 就不 bump"。"""
    monkeypatch.setenv("MLTO_OWM_SPLIT_DEDUPE", "false")
    monkeypatch.setattr(LB, "_has_postmortem", lambda *a, **k: True)
    monkeypatch.setattr(LB, "_has_owm_bump", lambda *a, **k: False)
    LB.record_outcome(None, _outcome(), None)
    assert spies["bump"] == 0
    assert spies["mark"] == 0


def test_failed_bump_is_not_marked(monkeypatch):
    """bump 失败（err:）不得写标记，否则会永久吞掉下一次机会。"""
    seen = {"mark": 0}
    monkeypatch.setattr(LB, "_has_postmortem", lambda *a, **k: False)
    monkeypatch.setattr(LB, "_has_owm_bump", lambda *a, **k: False)
    monkeypatch.setattr(LB, "_bump_owm", lambda *a, **k: "err:no_db")
    monkeypatch.setattr(LB, "_mark_owm_bump", lambda *a, **k: seen.__setitem__("mark", seen["mark"] + 1))
    _install_fake_thesis_store(monkeypatch, {"pm_write": 0})
    monkeypatch.setenv("MLTO_OWM_SPLIT_DEDUPE", "true")
    LB.record_outcome(None, _outcome(), None)
    assert seen["mark"] == 0


@pytest.mark.parametrize("val,expected", [("true", True), ("1", True), ("", True), ("false", False), ("0", False), ("off", False)])
def test_split_dedupe_switch(monkeypatch, val, expected):
    monkeypatch.setenv("MLTO_OWM_SPLIT_DEDUPE", val)
    assert LB._split_dedupe() is expected
