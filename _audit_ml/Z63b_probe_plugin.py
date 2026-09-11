# -*- coding: utf-8 -*-
"""Z63b: 探针插件 —— 若 test_executors 的两个失败用例调用了本轮新增代码路径，则立即失败。"""
import pytest

import backend.services.trend_e1_engine as E

CALLED = []

def _boom(*a, **k):
    CALLED.append(1)
    raise AssertionError("本轮新增代码路径被调用（改判：失败可能与本轮改动相关）")

@pytest.fixture(autouse=True)
def _probe_new_paths(monkeypatch):
    monkeypatch.setattr(E, "scheduled_job", _boom, raising=False)
    monkeypatch.setattr(E, "_pre_exec_live_gate", _boom, raising=False)
    yield
    assert not CALLED, f"新增路径被调用 {len(CALLED)} 次"
