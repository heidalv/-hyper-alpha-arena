"""
FactorSignalGenerator — 因子信号生成层

将 Dict[str, FactorValue] 转换为标准化交易信号：
- direction: [-1.0, +1.0]（看空 ~ 看多）
- strength:  [0.0, 1.0]（信号强度）
- confidence: [0.0, 1.0]（因子方向一致性）
"""

import logging
import math
from dataclasses import dataclass, field
from typing import Callable, Dict, List, Optional, Tuple

from .base_factors import FactorCategory, FactorValue

logger = logging.getLogger(__name__)


# ════════════════════════════════════════════════════════════
#  数据结构
# ════════════════════════════════════════════════════════════

@dataclass
class FactorSignal:
    """单个因子的标准化信号"""
    factor_id: str
    direction: float   # [-1.0, +1.0]，负=看空，正=看多
    strength: float    # [0.0, 1.0]，信号强度
    category: str


@dataclass
class CompositeSignal:
    """多因子加权合成信号"""
    direction: float                   # [-1.0, +1.0]
    strength: float                    # [0.0, 1.0]
    confidence: float                  # [0.0, 1.0]
    contributing_factors: int
    regime: str
    signals: Dict[str, FactorSignal] = field(default_factory=dict)


# ════════════════════════════════════════════════════════════
#  方向映射器
# ════════════════════════════════════════════════════════════

def _rsi_direction(value: float) -> float:
    """RSI: <30 看多（超卖），>70 看空（超买）"""
    return max(-1.0, min(1.0, (50.0 - value) / 50.0))


def _macd_direction(value: float) -> float:
    """MACD: >0 看多，<0 看空"""
    return max(-1.0, min(1.0, value / max(abs(value), 1e-8) * min(1.0, abs(value) * 10)))


def _momentum_direction(value: float) -> float:
    """动量: 正=看多，负=看空"""
    return max(-1.0, min(1.0, value / 5.0))


def _ema_trend_direction(value: float) -> float:
    """EMA 趋势: 正=看多，负=看空"""
    return max(-1.0, min(1.0, float(value)))


def _supertrend_direction(value: float) -> float:
    """SuperTrend: >0 看多，<0 看空"""
    return max(-1.0, min(1.0, float(value)))


def _bb_zscore_direction(value: float) -> float:
    """布林带 Z-Score: 低于均值看多，高于均值看空（均值回归）"""
    return max(-1.0, min(1.0, -value / 4.0))


def _funding_rate_direction(value: float) -> float:
    """资金费率: 极端正值=市场过热→看空，极端负值=市场恐慌→看多（反向）。

    [2026-08-14 P1-E1 修复] 单位约定：value 为**百分比数值**（0.01 = 0.01%，
    由 `base_factors.compute_funding_rate` 对十进制费率单次 ×100 转换）。
    饱和点 0.05% → 满格看空。旧实现 `-value*100` 对正常费率(0.01%~0.1%)
    恒饱和为 -1.0，给合成信号注入恒定满格空头偏置（|direction|=1 恒进 top-N）。
    回滚开关：FACTOR_FUNDING_DIRECTION_FIX=false。
    """
    try:
        from backend.config import settings as _s
        if not bool(getattr(_s, "FACTOR_FUNDING_DIRECTION_FIX", True)):
            return max(-1.0, min(1.0, -float(value) * 100.0))
    except Exception:
        pass
    return max(-1.0, min(1.0, -float(value) / 0.05))


def _adx_direction(value: float) -> float:
    """ADX: 无方向性，高值=强趋势→用正值表示强信号"""
    # ADX > 25 表示趋势市，ADX < 20 表示盘整
    # 方向由其他因子决定，此处仅表示趋势强度（归一化到 [-1,+1]）
    return 0.0  # ADX 本身无方向


def _atr_direction(value: float) -> float:
    """ATR/ATR Ratio: 无方向性，仅表示波动率大小"""
    return 0.0


def _hv_direction(value: float) -> float:
    """历史波动率: 无方向性"""
    return 0.0


def _parkinson_vol_direction(value: float) -> float:
    """Parkinson 波动率: 无方向性"""
    return 0.0


def _obv_direction(value: float) -> float:
    """OBV: 正=买盘主导→看多，负=卖盘主导→看空"""
    return max(-1.0, min(1.0, value / 5.0))


def _vwap_direction(value: float) -> float:
    """VWAP 偏离: 正=价格在VWAP上方→看多，负=在下方→看空"""
    return max(-1.0, min(1.0, float(value)))


def _volume_zscore_direction(value: float) -> float:
    """成交量 Z-Score: 无方向性，高值仅表示放量"""
    return 0.0


def _cvd_ratio_direction(value: float) -> float:
    """CVD 比率: 正=买盘净流入→看多，负=卖盘净流出→看空"""
    return max(-1.0, min(1.0, float(value)))


def _oi_delta_direction(value: float) -> float:
    """OI 变化: 需结合价格方向判断，此处正值=持仓增加"""
    # OI 本身需结合价格方向，单看无法判断多空
    return max(-1.0, min(1.0, float(value)))


def _taker_ratio_direction(value: float) -> float:
    """Taker 比率: 正=主动买入主导→看多，负=主动卖出主导→看空"""
    return max(-1.0, min(1.0, float(value)))


def _default_direction(value: float) -> float:
    """默认映射: tanh 归一化。

    [fix] 旧逻辑 min(1,value)/max(-1,value) 把任何 |value|>=1 的因子都压成满格 ±1，
    导致 37+ 个因子满格、多空对冲后互相抵消，direction 被压到 0.1 量级（score<30 全天 hold）。
    tanh 保留强度区分：tanh(0.1)≈0.1、tanh(1)≈0.76、tanh(3)≈0.995，
    小值信号不再被满格因子淹没。
    """
    try:
        v = float(value)
    except (TypeError, ValueError):
        return 0.0
    import math
    return max(-1.0, min(1.0, math.tanh(v)))


# ════════════════════════════════════════════════════════════
#  FactorSignalGenerator
# ════════════════════════════════════════════════════════════

def _cap_category_share(
    selected: List[Tuple[str, "FactorSignal", float]],
    eff_weights: List[float],
) -> List[float]:
    """把单一 FactorCategory 的合计有效权重限制在 FACTOR_CATEGORY_MAX_SHARE 以内。

    动机：top-N 聚合按 |direction| 排序截断，如果同一类别（比如 momentum 下的
    RSI/MACD/Momentum/ROC）恰好都强烈同向，会在 15 个名额里占掉大半，本质上是
    同一个"动量观点"被计权 4-5 次，而不是拿到 4-5 个独立信息源，导致合成信号的
    置信度被虚增。这里做类内等比例缩放（不改变类内相对强弱），把该类别的合计
    权重压到上限，让位给其他类别的信息，从而更接近"多个独立信号源"的假设。

    详见 docs/SCALP_FACTOR_STRATEGY_ANALYSIS_2026-07-06.md 第2.1节。
    """
    try:
        from backend.config.settings import (
            FACTOR_CATEGORY_DEDUP_ENABLED,
            FACTOR_CATEGORY_MAX_SHARE,
        )
    except Exception:
        FACTOR_CATEGORY_DEDUP_ENABLED, FACTOR_CATEGORY_MAX_SHARE = True, 0.40

    if not FACTOR_CATEGORY_DEDUP_ENABLED or not eff_weights:
        return eff_weights

    total = sum(eff_weights)
    if total <= 0:
        return eff_weights

    cap = total * max(0.0, min(1.0, FACTOR_CATEGORY_MAX_SHARE))

    category_totals: Dict[str, float] = {}
    for (_, sig, _w), ew in zip(selected, eff_weights):
        category_totals[sig.category] = category_totals.get(sig.category, 0.0) + ew

    scale_by_category: Dict[str, float] = {}
    for cat, cat_total in category_totals.items():
        if cat_total > cap > 0:
            scale_by_category[cat] = cap / cat_total

    if not scale_by_category:
        return eff_weights

    return [
        ew * scale_by_category.get(sig.category, 1.0)
        for (_, sig, _w), ew in zip(selected, eff_weights)
    ]


class FactorSignalGenerator:
    """将因子值转换为标准化方向信号"""

    def __init__(self):
        self._direction_mappers: Dict[str, Callable[[float], float]] = {}
        self._register_default_mappers()

    def _register_default_mappers(self) -> None:
        """注册默认的因子方向映射"""
        self._direction_mappers = {
            # 动量因子
            "rsi": _rsi_direction,
            "macd": _macd_direction,
            "momentum": _momentum_direction,
            "roc": _momentum_direction,
            "adx": _adx_direction,
            # 均值回归因子
            "bb_width": _bb_zscore_direction,
            "zscore": _bb_zscore_direction,
            "atr_ratio": _atr_direction,
            # 波动率因子（无方向性）
            "atr": _atr_direction,
            "hv": _hv_direction,
            "parkinson_vol": _parkinson_vol_direction,
            # 成交量因子
            "obv": _obv_direction,
            "vwap": _vwap_direction,
            "volume_zscore": _volume_zscore_direction,
            "cvd_ratio": _cvd_ratio_direction,
            # 趋势因子
            "ema_trend": _ema_trend_direction,
            "sma_cross": _ema_trend_direction,
            "supertrend": _supertrend_direction,
            # 市场流向因子
            "taker_ratio": _taker_ratio_direction,
            "oi_delta": _oi_delta_direction,
            "funding_rate": _funding_rate_direction,
        }

    def register_mapper(self, factor_name: str, mapper: Callable[[float], float]) -> None:
        """注册自定义因子方向映射"""
        self._direction_mappers[factor_name] = mapper

    def generate_signals(
        self,
        factor_values: Dict[str, FactorValue],
        weights: Optional[Dict[str, float]] = None,
        regime: str = "unknown",
        symbol: Optional[str] = None,
        timeframe: Optional[str] = None,
    ) -> CompositeSignal:
        """
        从因子值生成合成信号。

        Args:
            factor_values: 因子名 -> FactorValue
            weights: 因子名 -> 权重（默认等权）
            regime: 当前市场状态标签
            symbol: 可选，合约/交易对，仅用于日志与追溯
            timeframe: 可选，时间周期，仅用于日志与追溯

        Returns:
            CompositeSignal 包含合成方向、强度、置信度
        """
        if not factor_values:
            return CompositeSignal(
                direction=0.0, strength=0.0, confidence=0.0,
                contributing_factors=0, regime=regime,
            )

        signals: Dict[str, FactorSignal] = {}
        # [2026-08-14 P1-E2 修复] 实盘路径补幽灵因子过滤：此前只有
        # factor_evaluation_pipeline._compute_weights 把 has_data=False /
        # is_directional=False 的因子权重设 0；本函数注释声称"配合 is_directional
        # 过滤幽灵因子"但循环体从未实现 → 价格/成交量绝对值类因子（value 恒正）
        # 被当成看多参与方向聚合，重新注入策略偏多。
        # 回滚开关：FACTOR_SIGNAL_FILTER_NONDIRECTIONAL=false。
        _filter_nondir = True
        try:
            from backend.config import settings as _s
            _filter_nondir = bool(getattr(_s, "FACTOR_SIGNAL_FILTER_NONDIRECTIONAL", True))
        except Exception:
            pass
        for name, fv in factor_values.items():
            if _filter_nondir:
                if getattr(fv, "has_data", True) is False:
                    continue
                if getattr(fv, "is_directional", True) is False:
                    continue
            # [fix] z-score 重做（timeframe 隔离后不再污染）：
            # 有专用 mapper 的因子用原始 value（有自己的语义归一化）；
            # 其余用 z-score 归一化后的 normalized（跨因子可比，放大方向强度）。
            if name in self._direction_mappers:
                direction = self._map_direction(name, fv.value)
            else:
                direction = max(-1.0, min(1.0, float(getattr(fv, "normalized", 0.0) or 0.0)))
            strength = self._compute_strength(direction)
            category = fv.category.value if isinstance(fv.category, FactorCategory) else str(fv.category)
            signals[name] = FactorSignal(
                factor_id=name,
                direction=direction,
                strength=strength,
                category=category,
            )

        # 权重默认等权
        if weights is None:
            weights = {name: 1.0 for name in signals}

        direction, strength, confidence = self._aggregate(signals, weights)

        return CompositeSignal(
            direction=direction,
            strength=strength,
            confidence=confidence,
            contributing_factors=len(signals),
            regime=regime,
            signals=signals,
        )

    def _map_direction(self, factor_name: str, value: float) -> float:
        """将因子原始值映射到 [-1, +1]"""
        mapper = self._direction_mappers.get(factor_name, _default_direction)
        try:
            result = mapper(value)
            return max(-1.0, min(1.0, float(result)))
        except (TypeError, ValueError, OverflowError):
            return 0.0

    def _compute_strength(self, direction: float) -> float:
        """信号强度 = |direction|"""
        return min(1.0, abs(direction))

    def _aggregate(
        self,
        signals: Dict[str, FactorSignal],
        weights: Dict[str, float],
    ) -> Tuple[float, float, float]:
        """
        加权聚合信号（top-N 强信号加权，跳过中性因子）。

        [fix 2026-06-30] 旧逻辑简单平均所有因子，37+ 个中性因子(|direction|<0.1)把
        分母撑大、稀释强信号，导致 score 永远 <10。改为只取 |direction| 最强的
        top-N 个因子加权平均，让明确方向的信号主导合成结果。

        Returns:
            (composite_direction, composite_strength, confidence)
        """
        # 1. 过滤掉权重<=0 和中性因子(|direction|<0.1)，按 |direction| 降序
        _TOP_N = 15
        _NEUTRAL_EPS = 0.1
        candidates = []
        for name, sig in signals.items():
            w = weights.get(name, 1.0)
            if w <= 0:
                continue
            if abs(sig.direction) < _NEUTRAL_EPS:
                continue  # 跳过中性因子，避免稀释
            candidates.append((name, sig, w))

        if not candidates:
            return 0.0, 0.0, 0.0

        # 2. 取方向最强的 top-N
        candidates.sort(key=lambda x: abs(x[1].direction), reverse=True)
        selected = candidates[:_TOP_N]

        # 3. 加权平均（权重 = 原权重 × |direction|，让强信号贡献更大）
        eff_weights: List[float] = [w * abs(sig.direction) for _, sig, w in selected]

        # [2026-07-06 新增] 同类因子去重：momentum 类常见 RSI/MACD/Momentum/ROC
        # 同时入选 top-15 时会重复表达同一个"动量观点"，虚增置信度。把单一
        # FactorCategory 的合计有效权重压到 FACTOR_CATEGORY_MAX_SHARE 以内，
        # 类内按原比例缩放，不改变类内因子的相对排序。
        eff_weights = _cap_category_share(selected, eff_weights)

        weighted_direction = 0.0
        weighted_strength = 0.0
        total_weight = 0.0
        directions: List[float] = []

        for (name, sig, w), eff_w in zip(selected, eff_weights):
            weighted_direction += sig.direction * eff_w
            weighted_strength += sig.strength * eff_w
            total_weight += eff_w
            directions.append(sig.direction)

        if total_weight == 0 or not directions:
            return 0.0, 0.0, 0.0

        composite_dir = weighted_direction / total_weight
        composite_str = weighted_strength / total_weight

        # confidence = 1 - std(directions) / max_possible_std
        # 完全一致时 confidence=1, 完全分散时 confidence→0
        if len(directions) < 2:
            confidence = min(1.0, composite_str)
        else:
            mean_d = sum(directions) / len(directions)
            variance = sum((d - mean_d) ** 2 for d in directions) / len(directions)
            std_d = math.sqrt(variance)
            # max std for [-1,1] is 1.0
            confidence = max(0.0, min(1.0, 1.0 - std_d))

        return (
            max(-1.0, min(1.0, composite_dir)),
            max(0.0, min(1.0, composite_str)),
            max(0.0, min(1.0, confidence)),
        )
