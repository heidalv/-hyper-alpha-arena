# -*- coding: utf-8 -*-
"""[P15 / §67 执行 2026-09-10] 遗留因子类别 `revN` 必须映射到 MEAN_REVERSION。

背景（§64.5）：`base_factors._resolve_category()` 对未知类别打 WARNING 后回退 `PATTERN`，
而 DB 里的 active 公式因子有一批类别写成 `rev5/rev10/rev20/rev50`（报告 §30 里那批
均值回归因子）。落 PATTERN 的后果：
  * `factor_selector._calculate_final_weight()` 类别系数 PATTERN=0.75 < MEAN_REVERSION=0.9；
  * scalp 热路径按 `exclude_categories={PATTERN, BEHAVIORAL}` 会把它们排除；
  * 每次加载刷一条"未知因子类别"告警，掩盖真正需要关注的类别问题。

修法：`^REV\\d+$` 统一映射到 MEAN_REVERSION（只改**类别标签**，不改任何因子表达式或数值）。
"""
from __future__ import annotations

import logging
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT))


def _factor_engine_cls():
    from backend.services.factor_engine.base_factors import FactorEngine, FactorCategory

    return FactorEngine, FactorCategory


def test_rev_categories_map_to_mean_reversion():
    FactorEngine, FactorCategory = _factor_engine_cls()
    engine = FactorEngine.__new__(FactorEngine)  # 只测解析逻辑，不触发构建
    for raw in ("rev5", "rev10", "rev20", "rev50", "REV240", "rev120"):
        assert engine._resolve_category(raw) is FactorCategory.MEAN_REVERSION, f"{raw} 未映射到 MEAN_REVERSION"


def test_rev_mapping_is_silent(caplog):
    FactorEngine, _ = _factor_engine_cls()
    engine = FactorEngine.__new__(FactorEngine)
    with caplog.at_level(logging.WARNING):
        engine._resolve_category("rev10")
    assert [r for r in caplog.records if "未知因子类别" in r.getMessage()] == [], \
        "revN 仍被当成未知类别（会刷告警并落 PATTERN）"


def test_unknown_categories_still_fall_back_with_warning(caplog):
    """负向保护：真·未知类别仍必须告警（不能借 P15 把告警一并关掉）。"""
    FactorEngine, FactorCategory = _factor_engine_cls()
    engine = FactorEngine.__new__(FactorEngine)
    with caplog.at_level(logging.WARNING):
        assert engine._resolve_category("totally_unknown_xyz") is FactorCategory.PATTERN
    assert any("未知因子类别" in r.getMessage() for r in caplog.records), \
        "未知类别不再告警 ⇒ 静默失效回归"


def test_existing_aliases_unchanged():
    FactorEngine, FactorCategory = _factor_engine_cls()
    engine = FactorEngine.__new__(FactorEngine)
    assert engine._resolve_category("technical") is FactorCategory.TREND
    assert engine._resolve_category("composite") is FactorCategory.STRENGTH
    assert engine._resolve_category("discovered") is FactorCategory.MOMENTUM
    assert engine._resolve_category("alpha101") is FactorCategory.MOMENTUM
