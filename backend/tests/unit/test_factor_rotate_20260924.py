# -*- coding: utf-8 -*-
"""[2026-09-24 新目标 R7] 因子批量计算的**公平轮转**单测。

背景（实测）：`v3_factor_pipeline` 原按固定顺序跑、超预算即 `break`
（最近 13 个周期里 11 个超时，每轮只算完 2~5 个币）⇒ 列表尾部永久饿死
（LINK 的 15m 复合因子 5 天未重算、XRP 9 小时）。
本测试锁定：轮转使每个币在有限轮内必被算到；关开关即恢复旧行为。
"""
from __future__ import annotations

from backend.services.full_auto import v3_factor_pipeline as V


def _reset():
    V._reset_rotate_cursor_for_test()


def test_rotation_covers_all_symbols(monkeypatch):
    monkeypatch.setenv("V3_FACTOR_ROTATE_ENABLED", "true")
    _reset()
    syms = [f"S{i}" for i in range(10)]
    seen = []
    for _ in range(5):                      # 每轮只算得完 4 个
        order = V._rotate_symbols(syms)
        seen.extend(order[:4])
        V._advance_rotate_cursor(len(syms), 4)
    assert set(seen) == set(syms), f"轮转后仍未覆盖全部币: 缺 {set(syms) - set(seen)}"


def test_rotation_disabled_keeps_fixed_order(monkeypatch):
    """回滚口径：关掉开关 ⇒ 永远从第一个开始（旧行为）。"""
    monkeypatch.setenv("V3_FACTOR_ROTATE_ENABLED", "false")
    _reset()
    syms = ["A", "B", "C", "D"]
    for _ in range(3):
        assert V._rotate_symbols(syms) == syms
        V._advance_rotate_cursor(len(syms), 2)
    assert V._ROTATE_STATE["cursor"] == 0


def test_rotation_no_progress_is_avoided(monkeypatch):
    """computed=0 时也要至少前进 1，避免卡死在同一批。"""
    monkeypatch.setenv("V3_FACTOR_ROTATE_ENABLED", "true")
    _reset()
    syms = ["A", "B", "C"]
    V._rotate_symbols(syms)
    V._advance_rotate_cursor(len(syms), 0)
    assert V._ROTATE_STATE["cursor"] == 1


def test_rotation_handles_empty_and_single(monkeypatch):
    monkeypatch.setenv("V3_FACTOR_ROTATE_ENABLED", "true")
    _reset()
    assert V._rotate_symbols([]) == []
    assert V._rotate_symbols(["A"]) == ["A"]
    V._advance_rotate_cursor(0, 0)
    V._advance_rotate_cursor(1, 1)
    assert V._ROTATE_STATE["cursor"] == 0


def test_budget_is_env_configurable(monkeypatch):
    """预算不再是硬编码 45：env 可调（默认仍 45）。"""
    import inspect
    src = inspect.getsource(V.run_v3_factor_pipeline)
    assert "V3_FACTOR_MAX_SECONDS" in src
    assert "_MAX_V3_SECONDS = 45\n" not in src, "不应再有硬编码 45"
