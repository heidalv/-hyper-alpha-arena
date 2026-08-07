"""
策略基因组 — 统一所有可进化参数的结构

将分散在各服务中的硬编码参数统一为一个可变异、可遗传的"基因组"。
每个参数定义为 (default, min, max, mutation_weight)。

参数定义统一从 strategy_params_registry 导入，本文件只负责操作函数。
"""

import copy
import math
import random
import logging
from typing import Any, Dict, Optional, Tuple

from backend.services.strategy_params_registry import (
    GENOME_SCHEMA,
    CATEGORY_SIGNAL_DEFAULTS,
    CATEGORY_KEY_PARAMS,
)

logger = logging.getLogger(__name__)

# 参数定义: (default, min, max, mutation_weight)
# mutation_weight 越大，进化时变异概率越高
ParamDef = Tuple[float, float, float, float]

# Legacy: 保留 TIER_OVERRIDES 以便旧代码 import 不报错，但内容为空
TIER_OVERRIDES: Dict[str, Dict[str, Any]] = {}

FLAT_DEFAULTS: Dict[str, float] = {}
FLAT_RANGES: Dict[str, Tuple[float, float, float]] = {}
for _group, _params in GENOME_SCHEMA.items():
    for _key, (_default, _min, _max, _weight) in _params.items():
        FLAT_DEFAULTS[_key] = _default
        FLAT_RANGES[_key] = (_min, _max, _weight)


def create_default_genome(category: str = "trend", **kwargs) -> Dict[str, Any]:
    """创建默认基因组，可根据策略类别微调

    Args:
        category: 策略类别 (trend/mean_reversion/breakout/momentum/swing/scalping)
        **kwargs: 向后兼容，接受但忽略 tier 等旧参数
    """
    genome = copy.deepcopy(FLAT_DEFAULTS)

    # 应用策略类别覆盖（从注册表读取）
    if category in CATEGORY_SIGNAL_DEFAULTS:
        genome.update(CATEGORY_SIGNAL_DEFAULTS[category])

    return genome


def mutate_genome(
    genome: Dict[str, Any],
    mutation_rate: float = 0.3,
    mutation_strength: float = 0.15,
    category: Optional[str] = None,
) -> Dict[str, Any]:
    """变异基因组，返回新基因组"""
    new_genome = copy.deepcopy(genome)

    # 类别相关参数变异概率更高（从注册表读取）
    boost_keys = set(CATEGORY_KEY_PARAMS.get(category or "", []))

    for key, value in list(new_genome.items()):
        if key not in FLAT_RANGES:
            continue
        lo, hi, weight = FLAT_RANGES[key]
        effective_rate = mutation_rate * weight
        if key in boost_keys:
            effective_rate *= 1.5

        if random.random() < effective_rate:
            span = hi - lo
            delta = random.gauss(0, span * mutation_strength)
            new_val = value + delta

            # 整数参数保持整数
            if isinstance(value, int) or key in (
                "ema_fast", "ema_mid", "ema_slow", "rsi_period", "atr_period",
                "bb_period", "macd_fast", "macd_slow", "macd_signal",
                "volume_ma_period", "breakout_lookback", "min_bars_between",
                "adx_period", "stoch_period", "stoch_smooth",
            ):
                new_val = int(round(new_val))
            new_genome[key] = max(lo, min(hi, new_val))

    # D7: 因子权重进化 — 对 factor_weights 子字典进行独立变异
    _fw = new_genome.get("factor_weights")
    if isinstance(_fw, dict) and _fw:
        _mutated_fw = {}
        for _fk, _fv in _fw.items():
            if random.random() < mutation_rate * 0.4:  # 因子权重变异概率略低
                _delta = random.uniform(-0.15, 0.15)
                _mutated_fw[_fk] = max(0.0, min(1.0, float(_fv) + _delta))
            else:
                _mutated_fw[_fk] = _fv
        new_genome["factor_weights"] = _mutated_fw

    return new_genome


def genome_to_signal_params(genome: Dict[str, Any]) -> Dict[str, Any]:
    """从基因组提取信号参数（供回测引擎使用）"""
    signal_keys = set(GENOME_SCHEMA.get("signal_params", {}).keys())
    return {k: v for k, v in genome.items() if k in signal_keys}


def genome_to_risk_params(genome: Dict[str, Any]) -> Dict[str, Any]:
    """从基因组提取风控参数"""
    risk_keys = set(GENOME_SCHEMA.get("risk_params", {}).keys())
    return {k: v for k, v in genome.items() if k in risk_keys}


def crossover_genomes(
    parent_a: Dict[str, Any],
    parent_b: Dict[str, Any],
    regime_scores_a: Optional[Dict[str, float]] = None,
    regime_scores_b: Optional[Dict[str, float]] = None,
) -> Dict[str, Any]:
    """轨迹级交叉：取两个策略各自强势 regime 参数段组合（QuantaAlpha 启发）

    如果没有 regime 分数信息，退化为均匀交叉。
    """
    child = copy.deepcopy(parent_a)
    groups = list(GENOME_SCHEMA.keys())

    if regime_scores_a and regime_scores_b:
        score_a = sum(regime_scores_a.values())
        score_b = sum(regime_scores_b.values())
        p_a = score_a / max(score_a + score_b, 0.001)

        for key in child:
            if key in FLAT_RANGES:
                if random.random() > p_a:
                    child[key] = parent_b.get(key, child[key])
    else:
        for key in child:
            if key in FLAT_RANGES and random.random() < 0.5:
                child[key] = parent_b.get(key, child[key])

    return child


def trajectory_mutate(
    genome: Dict[str, Any],
    trade_results: list = None,
    mutation_rate: float = 0.25,
) -> Dict[str, Any]:
    """轨迹级突变：对表现差的参数组做更大扰动（QuantaAlpha 启发）

    trade_results: [{"regime": str, "pnl": float}, ...]
    """
    new_genome = copy.deepcopy(genome)

    weak_groups = set()
    if trade_results:
        regime_pnl: Dict[str, float] = {}
        regime_count: Dict[str, int] = {}
        for t in trade_results:
            r = t.get("regime", "unknown")
            regime_pnl[r] = regime_pnl.get(r, 0) + t.get("pnl", 0)
            regime_count[r] = regime_count.get(r, 0) + 1
        for r, pnl in regime_pnl.items():
            if pnl < 0 and regime_count.get(r, 0) >= 3:
                weak_groups.add("risk_params")
                weak_groups.add("pipeline_weights")

    for key, value in list(new_genome.items()):
        if key not in FLAT_RANGES:
            continue
        lo, hi, weight = FLAT_RANGES[key]

        effective_rate = mutation_rate * weight
        is_weak = False
        for group_name, params in GENOME_SCHEMA.items():
            if key in params and group_name in weak_groups:
                is_weak = True
                effective_rate *= 2.0
                break

        if random.random() < effective_rate:
            span = hi - lo
            strength = 0.25 if is_weak else 0.15
            delta = random.gauss(0, span * strength)
            new_val = value + delta

            if isinstance(value, int) or key in (
                "ema_fast", "ema_mid", "ema_slow", "rsi_period",
                "bb_period", "macd_fast", "macd_slow", "macd_signal",
                "breakout_lookback", "min_bars_between", "confirmation_min_dims",
            ):
                new_val = int(round(new_val))
            new_genome[key] = max(lo, min(hi, new_val))

    # D7: 因子权重进化 — 对 factor_weights 子字典进行独立变异
    _fw = new_genome.get("factor_weights")
    if isinstance(_fw, dict) and _fw:
        _mutated_fw = {}
        for _fk, _fv in _fw.items():
            if random.random() < mutation_rate * 0.4:  # 因子权重变异概率略低
                _delta = random.uniform(-0.15, 0.15)
                _mutated_fw[_fk] = max(0.0, min(1.0, float(_fv) + _delta))
            else:
                _mutated_fw[_fk] = _fv
        new_genome["factor_weights"] = _mutated_fw

    return new_genome


def genome_distance(a: Dict[str, Any], b: Dict[str, Any]) -> float:
    """计算两个基因组之间的归一化距离（用于多样性检测）"""
    total = 0.0
    count = 0
    for key in FLAT_RANGES:
        lo, hi, _ = FLAT_RANGES[key]
        span = hi - lo
        if span <= 0:
            continue
        va = a.get(key, FLAT_DEFAULTS.get(key, 0))
        vb = b.get(key, FLAT_DEFAULTS.get(key, 0))
        total += ((va - vb) / span) ** 2
        count += 1
    return math.sqrt(total / max(count, 1))
