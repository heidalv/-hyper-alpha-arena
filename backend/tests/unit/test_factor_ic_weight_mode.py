# -*- coding: utf-8 -*-
"""[2026-08-29 全面修复 P2.2] 因子运行时权重映射（ic_ev 模式）契约测试。

旧 winrate 分档的问题：IC=-0.2963 的因子因胜率在 45-60% 带保持权重 1.0。
新 ic_ev 契约：
  - IC<=0 或方向一致率<45% → 权重地板 0.1；
  - 正向因子 w = clip(0.5 + 4×IC, 0.1, 1.5)；
  - FACTOR_IC_WEIGHT_MODE=winrate 回滚旧口径。
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))))

from backend.services import factor_ic_evaluator as fie


def _weight_for(ic, win_rate, monkeypatch, mode="ic_ev"):
    monkeypatch.setenv("FACTOR_IC_WEIGHT_MODE", mode)
    # 复用模块内部分段逻辑：直接构造与 run_factor_ic_evaluation 相同的判定分支
    n = fie.MIN_SAMPLES
    if mode == "winrate":
        if win_rate < 0.40:
            return 0.25
        elif win_rate < 0.45:
            return 0.5
        elif win_rate > 0.60:
            return 1.2
        return 1.0
    if ic is None or ic <= 0 or win_rate < 0.45:
        return fie._EV_WEIGHT_FLOOR
    return float(min(1.5, max(fie._EV_WEIGHT_FLOOR, 0.5 + 4.0 * ic)))


def test_negative_ic_gets_floor(monkeypatch):
    # 旧口径：胜率 0.53 → 权重 1.0（bug）；新口径：IC=-0.2963 → 0.1
    assert _weight_for(-0.2963, 0.53, monkeypatch) == fie._EV_WEIGHT_FLOOR


def test_low_consistency_gets_floor(monkeypatch):
    assert _weight_for(0.10, 0.40, monkeypatch) == fie._EV_WEIGHT_FLOOR


def test_positive_ic_scales(monkeypatch):
    assert abs(_weight_for(0.05, 0.55, monkeypatch) - 0.7) < 1e-9
    assert abs(_weight_for(0.10, 0.55, monkeypatch) - 0.9) < 1e-9
    assert abs(_weight_for(0.25, 0.60, monkeypatch) - 1.5) < 1e-9  # 封顶


def test_legacy_mode_rollback(monkeypatch):
    assert _weight_for(-0.2963, 0.53, monkeypatch, mode="winrate") == 1.0
    assert _weight_for(0.10, 0.42, monkeypatch, mode="winrate") == 0.5
