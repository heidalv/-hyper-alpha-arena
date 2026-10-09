# -*- coding: utf-8 -*-
"""[2026-09-24 R2 目标③] regime 缓存 TTL 可配 + 默认不变的单测。

依据（`scripts/audit_regime_lag_quant_20260924.py`，2026-09-24）：
  - 日线表含"形成中当日 bar"，close = 最新价（8 币 1d vs 当前 1h 差异 0.000%~0.235%）
    ⇒ 日线源不滞后；
  - 用实时价算 regime 比只用已收盘日线早**中位 14 小时**（28 币）⇒ 这部分已拿到；
  - 剩下的运行性滞后只有 TTL 本身（旧硬编码 1800s）。
  - 结构性滞后中位 **65 小时**，与 TTL 无关。
"""
from __future__ import annotations

import importlib

import pytest


@pytest.fixture()
def gate(monkeypatch):
    monkeypatch.setenv("MIDLONG_REGIME_TTL_S", "600")
    mod = importlib.import_module("backend.services.full_auto.midlong_circuit_gate")
    return importlib.reload(mod)


def test_ttl_default_is_1800_when_unset(monkeypatch):
    monkeypatch.delenv("MIDLONG_REGIME_TTL_S", raising=False)
    mod = importlib.reload(importlib.import_module("backend.services.full_auto.midlong_circuit_gate"))
    assert mod._regime_ttl_s() == 1800.0, "未设 env 时必须保持旧默认 1800，避免静默改变行为"


def test_ttl_reads_env(monkeypatch):
    monkeypatch.setenv("MIDLONG_REGIME_TTL_S", "600")
    mod = importlib.reload(importlib.import_module("backend.services.full_auto.midlong_circuit_gate"))
    assert mod._regime_ttl_s() == 600.0


def test_ttl_floor_and_garbage(monkeypatch):
    import backend.services.full_auto.midlong_circuit_gate as mod
    monkeypatch.setenv("MIDLONG_REGIME_TTL_S", "5")
    assert mod._regime_ttl_s() == 60.0, "下限 60s，防止把日线查询打到热路径上"
    monkeypatch.setenv("MIDLONG_REGIME_TTL_S", "abc")
    assert mod._regime_ttl_s() == 1800.0, "非法值退回默认"
    monkeypatch.setenv("MIDLONG_REGIME_TTL_S", "0")
    assert mod._regime_ttl_s() == 60.0, "0 视为下限"


def test_cache_uses_ttl_function(monkeypatch):
    """缓存判断必须走 _regime_ttl_s()，不能再引用硬编码常量。"""
    import inspect
    import backend.services.full_auto.midlong_circuit_gate as mod
    src = inspect.getsource(mod._daily_regime)
    assert "_regime_ttl_s()" in src
    assert "_DAILY_REGIME_TTL_S" not in src
