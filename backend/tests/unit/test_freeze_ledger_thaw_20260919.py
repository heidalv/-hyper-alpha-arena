# -*- coding: utf-8 -*-
"""轮115 「台账只冻不解」修复（2026-09-19）。

## 现场

`data/freeze_events.jsonl` 实测 **387 条事件、0 条解冻**：

```
kind 分布: {'freeze': 140, 'freeze_per_symbol': 247}
(0,   'scalp',          'BTC')  freeze=124 unfreeze=0
(188, 'per_symbol_risk','XPL')  freeze= 39 unfreeze=0
...
```

原因：解冻走的是**惰性过期**（`is_frozen()` 里直接 `_FREEZES.pop()`），**从来不写事件**；
唯一会写 `unfreeze` 的 `unfreeze()` 只被"修复链/人工"调用，实际从没被调到。
⇒ 任何读台账的人（含前端 `recent_events`）看到的都是"只冻不解、永远不解冻"。

本文件钉住：**状态翻转为未冻结的那一刻，必须补一条 `expire` 事件**（每个冻结只写一次），
且 `status()` 必须同时给出 freeze / 解冻两类计数，让"没记录"和"真冻着"可分辨。
"""
import io
import os
import sys
import time

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))))

_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from backend.services.risk_management import freeze_coordinator as FC  # noqa: E402
from backend.services.risk_management.portfolio_budget import portfolio_budget as PB  # noqa: E402


@pytest.fixture(autouse=True)
def _isolate():
    """快照/还原全局台账，避免污染其它测试与真实状态。"""
    fz = dict(FC._FREEZES)
    ev = list(FC._EVENT_LOG)
    kf = dict(PB._key_frozen_until)
    try:
        yield
    finally:
        FC._FREEZES.clear()
        FC._FREEZES.update(fz)
        FC._EVENT_LOG[:] = ev
        PB._key_frozen_until.clear()
        PB._key_frozen_until.update(kf)


def _arm_expired(acct=999, strat="swing", sym="BTC", why="selftest"):
    key = (acct, strat, sym)
    PB._key_frozen_until[key] = time.time() - 5
    FC._FREEZES[key] = {"until": time.time() - 5, "why": why, "scope": "key",
                        "frozen_at": time.time() - 100, "n": 1}
    return key


def test_lazy_expiry_is_recorded_as_thaw():
    _arm_expired()
    before = len(FC._EVENT_LOG)
    assert FC.is_frozen(999, "swing", "BTC") is False
    assert len(FC._EVENT_LOG) == before + 1, "解冻必须留下事件（否则台账只冻不解）"
    ev = FC._EVENT_LOG[-1]
    assert ev["kind"] == "expire"
    assert "selftest" in ev["why"], "解冻事件要能说出原原因"


def test_expiry_is_recorded_only_once():
    _arm_expired()
    FC.is_frozen(999, "swing", "BTC")
    n = len(FC._EVENT_LOG)
    for _ in range(3):
        assert FC.is_frozen(999, "swing", "BTC") is False
    assert len(FC._EVENT_LOG) == n, "已解冻的键不得反复写事件（热路径）"


def test_still_frozen_does_not_emit_thaw():
    key = (999, "swing", "BTC")
    PB._key_frozen_until[key] = time.time() + 600
    FC._FREEZES[key] = {"until": time.time() + 600, "why": "drawdown 9σ",
                        "scope": "key", "frozen_at": time.time(), "n": 1}
    n = len(FC._EVENT_LOG)
    assert FC.is_frozen(999, "swing", "BTC") is True
    assert len(FC._EVENT_LOG) == n, "冻结期内不得写解冻事件"


def test_status_reports_both_directions():
    _arm_expired()
    FC.is_frozen(999, "swing", "BTC")
    s = FC.status()
    assert "event_kinds" in s and "freeze_events" in s and "thaw_events" in s
    assert s["thaw_events"] >= 1
    assert s["event_kinds"].get("expire", 0) >= 1


def test_push_event_is_outside_the_lock():
    """结构证明：`_push_event` 必须在 `with _FREEZE_LOCK` **之外**调用。

    `_FREEZE_LOCK` 是普通 `threading.Lock`（不可重入），本文件 2026-09-02 已经
    因为"锁内调用 _push_event"自锁死过一次（台账整体卡住，含 is_frozen/unfreeze）。
    """
    src = io.open(os.path.join(_ROOT, "backend/services/risk_management/freeze_coordinator.py"),
                  encoding="utf-8").read()
    i = src.index("def is_frozen(")
    j = src.index("def status(")
    body = src[i:j]
    assert "_push_event(" in body, "解冻必须写事件"
    assert body.index("_push_event(") > body.index("with _FREEZE_LOCK:"), \
        "不得在锁内写事件（会自锁死）"


def test_evidence_is_documented_in_code():
    src = io.open(os.path.join(_ROOT, "backend/services/risk_management/freeze_coordinator.py"),
                  encoding="utf-8").read()
    assert "387 条事件、0 条解冻" in src, "必须把现场证据留在代码里（否则后人会再踩）"
