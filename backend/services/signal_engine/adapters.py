"""
Signal Adapters — 信号源适配器

将各种现有信号格式转换为统一的 SourceSignal 格式:
  - FactorSignalAdapter:    CompositeSignal -> SourceSignal
  - IntelSignalAdapter:     TradingDirectionSignal -> SourceSignal
  - ConfirmSignalAdapter:   ConfirmationResult -> SourceSignal
  - FusionSignalAdapter:    FusionDecision -> SourceSignal
"""

import time
import logging
from typing import Any, Callable, Dict, Optional

from .unified_signal import (
    SourceSignal,
    SOURCE_FACTOR, SOURCE_INTEL, SOURCE_CONFIRM, SOURCE_FUSION,
    SOURCE_NAMES,
    direction_to_action, clamp,
)

logger = logging.getLogger(__name__)


# ════════════════════════════════════════════════════════════
#  方向字符串 -> float 映射
# ════════════════════════════════════════════════════════════

_DIR_MAP = {
    "bullish": 1.0,
    "bearish": -1.0,
    "neutral": 0.0,
}


# ════════════════════════════════════════════════════════════
#  适配器实现
# ════════════════════════════════════════════════════════════

def adapt_factor_signal(composite: Any, symbol: str = "") -> SourceSignal:
    """适配 FactorSignalGenerator 的 CompositeSignal"""
    direction = clamp(float(getattr(composite, "direction", 0.0)), -1.0, 1.0)
    confidence = clamp(float(getattr(composite, "confidence", 0.0)), 0.0, 1.0)
    strength = clamp(float(getattr(composite, "strength", 0.0)), 0.0, 1.0)
    regime = getattr(composite, "regime", "unknown") or "unknown"
    contributing = getattr(composite, "contributing_factors", 0)

    # 提取 top-3 因子
    signals_dict = getattr(composite, "signals", {}) or {}
    top_factors = []
    for fid, fsig in sorted(
        signals_dict.items(),
        key=lambda x: abs(getattr(x[1], "direction", 0.0)),
        reverse=True,
    )[:3]:
        top_factors.append({
            "id": fid,
            "direction": getattr(fsig, "direction", 0.0),
            "category": getattr(fsig, "category", ""),
        })

    return SourceSignal(
        source_id=SOURCE_FACTOR,
        source_name=SOURCE_NAMES[SOURCE_FACTOR],
        direction=direction,
        confidence=confidence,
        strength=strength,
        weight=0.35,
        action=direction_to_action(direction, threshold=0.3),
        timestamp=time.time(),
        raw_data={
            "contributing_factors": contributing,
            "regime": regime,
            "top_factors": top_factors,
        },
    )


def adapt_intel_signal(signal: Any) -> SourceSignal:
    """适配 IntelligenceSignalEngine 的 TradingDirectionSignal"""
    raw_dir = getattr(signal, "direction", "neutral") or "neutral"
    direction = clamp(_DIR_MAP.get(raw_dir, 0.0), -1.0, 1.0)

    raw_conf = getattr(signal, "confidence", 0) or 0
    confidence = clamp(float(raw_conf) / 100.0, 0.0, 1.0)
    strength = abs(direction)

    # 提取子信号数据
    funding = getattr(signal, "funding", None)
    oi = getattr(signal, "oi", None)

    return SourceSignal(
        source_id=SOURCE_INTEL,
        source_name=SOURCE_NAMES[SOURCE_INTEL],
        direction=direction,
        confidence=confidence,
        strength=strength,
        weight=0.30,
        action=direction_to_action(direction, threshold=0.2),
        timestamp=time.time(),
        raw_data={
            "risk_level": getattr(signal, "risk_level", "normal"),
            "whale_direction": getattr(signal, "whale_direction", 0.0),
            "news_sentiment": getattr(signal, "news_sentiment", 0.0),
            "fear_greed_index": getattr(signal, "fear_greed_index", 50),
            "funding_signal": getattr(funding, "signal", None) if funding else None,
            "funding_regime": getattr(funding, "regime", None) if funding else None,
            "oi_quadrant": getattr(oi, "quadrant", None) if oi else None,
            "oi_signal": getattr(oi, "signal", None) if oi else None,
        },
    )


def adapt_confirm_signal(result: Any) -> SourceSignal:
    """适配 SignalConfirmationEngine 的 ConfirmationResult"""
    direction = clamp(float(getattr(result, "direction", 0)), -1.0, 1.0)
    strength = clamp(float(getattr(result, "strength", 0.0)), 0.0, 1.0)
    pos_mul = float(getattr(result, "position_multiplier", 1.0))
    confidence = strength * pos_mul

    raw_action = getattr(result, "action", "HOLD") or "HOLD"
    action = {"BUY": "buy", "SELL": "sell", "HOLD": "hold"}.get(raw_action, "hold")

    # 维度分解
    dims = getattr(result, "dimensions", {}) or {}
    dim_summary = {}
    for dk, dv in dims.items():
        dim_summary[dk] = {
            "direction": getattr(dv, "direction", 0),
            "strength": getattr(dv, "strength", 0.0),
            "reason": getattr(dv, "reason", ""),
        }

    return SourceSignal(
        source_id=SOURCE_CONFIRM,
        source_name=SOURCE_NAMES[SOURCE_CONFIRM],
        direction=direction,
        confidence=clamp(confidence, 0.0, 1.0),
        strength=strength,
        weight=0.20,
        action=action,
        timestamp=time.time(),
        raw_data={
            "confirmation_level": getattr(result, "confirmation_level", "none"),
            "position_multiplier": pos_mul,
            "confirmed_dimensions": getattr(result, "confirmed_dimensions", 0),
            "dimensions": dim_summary,
        },
    )


def adapt_fusion_signal(decision: Any) -> SourceSignal:
    """适配 DecisionFusionEngine 的 FusionDecision"""
    direction = clamp(float(getattr(decision, "signal_direction", 0.0)), -1.0, 1.0)
    confidence = clamp(float(getattr(decision, "confidence", 0.0)), 0.0, 1.0)
    strength = clamp(float(getattr(decision, "signal_strength", 0.0)), 0.0, 1.0)

    raw_action = getattr(decision, "action", "hold") or "hold"
    action = raw_action.lower() if raw_action.lower() in ("buy", "sell", "hold", "close") else "hold"
    if action == "close":
        action = "hold"

    # top factors
    details = getattr(decision, "factor_details", {}) or {}
    top_factors = []
    for fid, fsig in sorted(
        details.items(),
        key=lambda x: abs(getattr(x[1], "direction", 0.0)),
        reverse=True,
    )[:3]:
        top_factors.append({
            "id": fid,
            "direction": getattr(fsig, "direction", 0.0),
        })

    return SourceSignal(
        source_id=SOURCE_FUSION,
        source_name=SOURCE_NAMES[SOURCE_FUSION],
        direction=direction,
        confidence=confidence,
        strength=strength,
        weight=0.15,
        action=action,
        timestamp=time.time(),
        raw_data={
            "data_quality": getattr(decision, "data_quality", "unknown"),
            "regime": getattr(decision, "regime", "unknown"),
            "reasoning": getattr(decision, "reasoning", ""),
            "top_factors": top_factors,
        },
    )


# ════════════════════════════════════════════════════════════
#  适配器管理器
# ════════════════════════════════════════════════════════════

class SignalAdapterManager:
    """信号适配器管理器 — 按源 ID 分发到对应的适配器"""

    def __init__(self):
        self._adapters: Dict[str, Callable] = {
            SOURCE_FACTOR: adapt_factor_signal,
            SOURCE_INTEL: adapt_intel_signal,
            SOURCE_CONFIRM: adapt_confirm_signal,
            SOURCE_FUSION: adapt_fusion_signal,
        }

    def register(self, source_id: str, adapter_fn: Callable) -> None:
        """注册自定义适配器"""
        self._adapters[source_id] = adapter_fn

    def adapt(self, source_id: str, raw_signal: Any, **kwargs) -> Optional[SourceSignal]:
        """将原始信号转换为 SourceSignal"""
        adapter_fn = self._adapters.get(source_id)
        if adapter_fn is None:
            logger.debug(f"[Adapter] 无适配器: {source_id}")
            return None
        try:
            return adapter_fn(raw_signal, **kwargs)
        except Exception as e:
            logger.debug(f"[Adapter] {source_id} 适配失败: {e}")
            return None


# 模块级单例
signal_adapter_manager = SignalAdapterManager()
