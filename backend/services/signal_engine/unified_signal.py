"""
Unified Signal — 统一信号数据模型

将 5 套割裂的信号体系（FactorSignalGenerator、IntelligenceSignalEngine、
SignalConfirmationEngine、DecisionFusionEngine）归一化为统一格式。

归一化规则:
  direction:   统一为 float [-1.0, +1.0]
  confidence:  统一为 float [0.0, 1.0]
  strength:    统一为 float [0.0, 1.0]
  action:      统一为 str "buy" | "sell" | "hold"
"""

import time
from dataclasses import dataclass, field
from typing import Dict, Optional


# ════════════════════════════════════════════════════════════
#  信号源名称常量
# ════════════════════════════════════════════════════════════

SOURCE_FACTOR = "factor"
SOURCE_INTEL = "intel"
SOURCE_CONFIRM = "confirm"
SOURCE_FUSION = "fusion"

SOURCE_NAMES = {
    SOURCE_FACTOR: "因子引擎",
    SOURCE_INTEL: "情报汇流",
    SOURCE_CONFIRM: "三维确认",
    SOURCE_FUSION: "决策融合",
}

# 共振等级
CONFLUENCE_STRONG_RESONANCE = "strong_resonance"
CONFLUENCE_RESONANCE = "resonance"
CONFLUENCE_NEUTRAL = "neutral"
CONFLUENCE_CONFLICT = "conflict"
CONFLUENCE_STRONG_CONFLICT = "strong_conflict"

# 动作
ACTION_BUY = "buy"
ACTION_SELL = "sell"
ACTION_HOLD = "hold"


# ════════════════════════════════════════════════════════════
#  数据结构
# ════════════════════════════════════════════════════════════

@dataclass
class SourceSignal:
    """单源信号分解 — 每个信号源归一化后的标准输出"""
    source_id: str              # "factor" | "intel" | "confirm" | "fusion"
    source_name: str            # 显示名（中文）
    direction: float            # [-1.0, +1.0]
    confidence: float           # [0.0, 1.0]
    strength: float             # [0.0, 1.0]
    weight: float               # 在融合中的权重
    action: Optional[str]       # "buy" | "sell" | "hold"
    timestamp: float
    raw_data: Optional[dict] = None


@dataclass
class UnifiedSignal:
    """统一融合信号 — 多源加权聚合后的最终信号"""
    symbol: str
    direction: float            # [-1.0, +1.0] 最终融合方向
    confidence: float           # [0.0, 1.0]
    strength: float             # [0.0, 1.0]
    action: str                 # "buy" | "sell" | "hold"
    confluence_level: str       # 共振等级
    source_count: int
    agreeing_sources: int       # 方向一致的源数量
    conflicting_sources: int    # 方向冲突的源数量
    sources: Dict[str, SourceSignal] = field(default_factory=dict)
    regime: str = "unknown"
    reasoning: str = ""
    timestamp: float = 0.0
    cache_ttl: float = 45.0


# ════════════════════════════════════════════════════════════
#  辅助函数
# ════════════════════════════════════════════════════════════

def direction_to_action(direction: float, threshold: float = 0.2) -> str:
    """方向值转为交易动作"""
    if direction > threshold:
        return ACTION_BUY
    elif direction < -threshold:
        return ACTION_SELL
    return ACTION_HOLD


def clamp(value: float, lo: float, hi: float) -> float:
    """限制值范围"""
    return max(lo, min(hi, value))


def make_empty_signal(symbol: str) -> UnifiedSignal:
    """创建空信号（无源数据时的兜底）"""
    return UnifiedSignal(
        symbol=symbol,
        direction=0.0,
        confidence=0.0,
        strength=0.0,
        action=ACTION_HOLD,
        confluence_level=CONFLUENCE_NEUTRAL,
        source_count=0,
        agreeing_sources=0,
        conflicting_sources=0,
        timestamp=time.time(),
    )
