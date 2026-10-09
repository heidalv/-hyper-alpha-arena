# -*- coding: utf-8 -*-
"""[2026-09-24 新目标 R24] OWM 接线失败告警的限流单测。

背景：`brain.py` 里 OWM→conviction 的 fail-open 分支原为 logger.debug。
实测 backend.log 覆盖 37 小时、`MidLongBrain` 18364 次，而 `MidLongBrain] OWM` **0 次**
⇒ 该块从未成功执行，失败被静默吞掉。现改为限流 WARNING。
"""
from __future__ import annotations

import pytest

from backend.services.mlto import brain as B


@pytest.fixture(autouse=True)
def _reset_throttle(monkeypatch):
    monkeypatch.setattr(B, "_OWM_FAIL_LAST_TS", float("-inf"), raising=False)
    monkeypatch.delenv("MLTO_OWM_FAIL_WARN_SEC", raising=False)
    yield


@pytest.mark.parametrize("val,expected", [("true", True), ("1", True), ("", True), ("false", False), ("0", False), ("off", False)])
def test_warn_switch(monkeypatch, val, expected):
    monkeypatch.setenv("MLTO_OWM_FAIL_WARN", val)
    assert B._owm_fail_warn_enabled() is expected


def test_first_failure_logs_then_throttles():
    assert B._owm_fail_should_log(1000.0) is True      # 首次必打
    assert B._owm_fail_should_log(1001.0) is False     # 300s 内不再打
    assert B._owm_fail_should_log(1299.0) is False
    assert B._owm_fail_should_log(1300.0) is True      # 到点再打


def test_throttle_window_configurable(monkeypatch):
    monkeypatch.setenv("MLTO_OWM_FAIL_WARN_SEC", "10")
    assert B._owm_fail_should_log(0.0) is True
    assert B._owm_fail_should_log(5.0) is False
    assert B._owm_fail_should_log(10.0) is True


def test_invalid_window_falls_back_to_300(monkeypatch):
    monkeypatch.setenv("MLTO_OWM_FAIL_WARN_SEC", "garbage")
    assert B._owm_fail_should_log(0.0) is True
    assert B._owm_fail_should_log(299.0) is False
    assert B._owm_fail_should_log(300.0) is True
