"""P0-1/P0-2 修复回归测试（2026-08-14）。

锁定：
- P0-1 DSR/PBO 闸门：跨币样本不足时 fail-open 跳过（单币/3币一致化），
  样本充足时正常判定；PBO 计算返回显式 indeterminate。
- P0-2 键归一化 / 类别别名映射 / 白名单优先语义。
- P1-A1 evaluator 前瞻期随调用更新。
"""
from __future__ import annotations

import logging

import numpy as np
import pandas as pd
import pytest


# ═══════════════════════════════════════════════════════════
# P0-1：DSR/PBO 闸门
# ═══════════════════════════════════════════════════════════

def test_pbo_simple_indeterminate_for_small_sample():
    from backend.services.factor_engine.dsr_pbo import compute_pbo_simple

    r = compute_pbo_simple([0.1, 0.2, 0.3])  # 3 个 ICIR（BTC/ETH/SOL 场景）
    assert r["pbo"] == 0.5                      # 展示值保留
    assert r["indeterminate"] is True           # 但必须显式标记不可判定
    assert r["significant"] is False


def test_pbo_simple_not_indeterminate_for_large_sample():
    from backend.services.factor_engine.dsr_pbo import compute_pbo_simple

    r = compute_pbo_simple([0.1, 0.2, 0.3, 0.4, 0.5, 0.6, 0.7, 0.8])
    assert r["indeterminate"] is False
    assert "pbo" in r


def test_dsr_gate_skips_open_for_three_symbols(caplog):
    """3 币 ICIR 场景：必须 fail-open 跳过（修复前恒 False 锁死晋升）。"""
    from backend.services.factor_engine.factor_backtest_scorer import factor_backtest_scorer

    with caplog.at_level(logging.WARNING):
        dsr_ok, pbo = factor_backtest_scorer._dsr_pbo_gate([0.1, 0.2, 0.3], 900, 40)
    assert dsr_ok is True
    assert pbo is None                          # None = 显式跳过标记
    assert any("跳过" in m for m in caplog.messages) or any(
        "DSR/PBO" in m for m in caplog.messages
    )


def test_dsr_gate_single_symbol_same_semantics(caplog):
    """单币与 3 币行为一致化（同为 fail-open 跳过，不再走两套分支）。"""
    from backend.services.factor_engine.factor_backtest_scorer import factor_backtest_scorer

    with caplog.at_level(logging.WARNING):
        dsr_ok, pbo = factor_backtest_scorer._dsr_pbo_gate([0.2], 900, 40)
    assert dsr_ok is True
    assert pbo is None


def test_dsr_gate_runs_with_sufficient_symbols():
    """样本充足（>=4 币）时正常走 DSR/PBO 判定，pbo 为数值而非 None。"""
    from backend.services.factor_engine.factor_backtest_scorer import factor_backtest_scorer

    dsr_ok, pbo = factor_backtest_scorer._dsr_pbo_gate(
        [0.10, 0.22, 0.31, 0.44, 0.53], 900, 40,
    )
    assert isinstance(dsr_ok, bool)
    assert pbo is not None
    assert 0.0 <= float(pbo) <= 1.0


# ═══════════════════════════════════════════════════════════
# P0-2：键归一化 / 类别映射 / 白名单优先
# ═══════════════════════════════════════════════════════════

def test_normalize_engine_key():
    from backend.services.factor_engine.key_utils import normalize_engine_key

    assert normalize_engine_key("evo_d6f82d364676127e") == "d6f82d364676127e"
    assert normalize_engine_key("d6f82d364676127e") == "d6f82d364676127e"
    assert normalize_engine_key("ai_a101_mom_4h") == "ai_a101_mom_4h"
    assert normalize_engine_key("s5m_abc") == "s5m_abc"


def test_category_alias_mapping():
    """旧分类字符串不再静默落 PATTERN。"""
    from backend.services.factor_engine.base_factors import FactorCategory, factor_engine

    assert factor_engine._resolve_category("technical") is FactorCategory.TREND
    assert factor_engine._resolve_category("composite") is FactorCategory.STRENGTH
    assert factor_engine._resolve_category("discovered") is FactorCategory.MOMENTUM
    assert factor_engine._resolve_category("alpha101") is FactorCategory.MOMENTUM
    # 枚举直通不受影响
    assert factor_engine._resolve_category("funding") is FactorCategory.FUNDING


def test_category_unknown_warns(caplog):
    import logging as _lg
    from backend.services.factor_engine.base_factors import FactorCategory, factor_engine

    with caplog.at_level(_lg.WARNING):
        cat = factor_engine._resolve_category("totally_unknown_xyz")
    assert cat is FactorCategory.PATTERN
    assert any("未知因子类别" in m for m in caplog.messages)


def test_allowlist_priority_over_exclude():
    """白名单命中的因子不受类别排除影响（P0-2 核心语义）。"""
    from backend.services.factor_engine.base_factors import (
        FactorCategory,
        FactorValue,
        factor_engine,
    )

    saved = dict(factor_engine.FACTORS)
    try:
        factor_engine.FACTORS.clear()
        factor_engine.FACTORS["evo_vetted_1"] = {
            "category": FactorCategory.PATTERN,   # 旧逻辑会被 exclude 拦掉
            "name": "evo_vetted_1",
            "compute": lambda klines, market_data=None: 1.0,
        }
        factor_engine.FACTORS["pattern_junk"] = {
            "category": FactorCategory.PATTERN,
            "name": "pattern_junk",
            "compute": lambda klines, market_data=None: 1.0,
        }
        klines = pd.DataFrame({"close": np.linspace(100, 110, 30)})
        out = factor_engine.compute_all_factors(
            klines,
            {"symbol": "BTC", "timeframe": "5m"},
            exclude_categories={FactorCategory.PATTERN, FactorCategory.BEHAVIORAL},
            allowlist={"vetted_1"},   # 裸 id（无 evo_ 前缀），命中 evo_vetted_1
        )
        assert "evo_vetted_1" in out          # 白名单命中 → 不被 PATTERN 排除
        assert "pattern_junk" not in out      # 非白名单 → 被 allowlist 拦截
        assert isinstance(out["evo_vetted_1"], FactorValue)
    finally:
        factor_engine.FACTORS.clear()
        factor_engine.FACTORS.update(saved)


# ═══════════════════════════════════════════════════════════
# P1-A1：evaluator 前瞻期
# ═══════════════════════════════════════════════════════════

def test_evaluator_singleton_forward_period_updates():
    from backend.services.factor_engine.factor_evaluator import get_factor_evaluator

    ev1 = get_factor_evaluator(forward_period=6)
    assert ev1.forward_period == 6
    ev2 = get_factor_evaluator(forward_period=2)   # 第二次调用必须生效
    assert ev2 is ev1
    assert ev2.forward_period == 2
