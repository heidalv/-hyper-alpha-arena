"""MLTO shared types."""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Dict, List, Optional


@dataclass
class Signal:
    name: str
    value: float  # 0-1
    confidence: float  # 0-1
    source: str  # framework | llm | debate


@dataclass
class HubDecision:
    action: str  # WAIT | NIBBLE | BUILD
    direction: str  # long | short | neutral
    composite: float
    adjusted: float
    consistency: float
    open_readiness: int
    reason_text: str
    signals: List[Signal] = field(default_factory=list)
    # [2026-08-05 v6 6.3] ai_governed 模式标记："standard" / "ai_governed"；
    # ai_governed_weight=灰度权重档位（0.40/0.60/1.0，standard 下为 None）
    mode: str = "standard"
    ai_governed_weight: Optional[float] = None

    def direction_to_action(self) -> str:
        if self.action in ("WAIT",) or self.direction == "neutral":
            return "hold"
        if self.direction == "long":
            return "buy"
        if self.direction == "short":
            return "sell"
        return "hold"


@dataclass
class MemoryEventDTO:
    event_id: str
    thesis_id: str
    layer: str
    source: str
    signal: str
    summary: str
    gamma: float = 0.0
    ts: Optional[datetime] = None
    raw_payload: Dict[str, Any] = field(default_factory=dict)
    cited_by_llm: bool = False
    # [阶段2] 衰减层级覆盖：None=用 thesis.tier（现状）；"mid"=用中周期衰减曲线
    # （用于长线 thesis 里 mid_view 子分析的事件，使其 2h 浅层衰减而非 6h）。
    decay_tier: Optional[str] = None


@dataclass
class MidViewDTO:
    """中周期(1h/4h)子视图，嵌入长线 thesis。中周期只做择时，不做方向。

    阶段2 合并基础：mid 分析以子结构形式 living inside 一个 long thesis。
    direction 是相对长线方向的（align/counter/neutral），不是独立方向。
    """
    direction: str = "neutral"            # align|counter|neutral (相对长线方向)
    timing_score: int = 50                # 0-100，择时分（高=好入场时机）
    timing_rationale: str = ""
    key_levels: Optional[Dict[str, Any]] = None  # {support, resistance}
    invalidation_for_timing: str = ""     # 中周期择时失效条件
    updated_at: float = 0.0

    def to_dict(self) -> Dict[str, Any]:
        return {
            "direction": self.direction,
            "timing_score": self.timing_score,
            "timing_rationale": self.timing_rationale,
            "key_levels": self.key_levels,
            "invalidation_for_timing": self.invalidation_for_timing,
            "updated_at": self.updated_at,
        }

    @classmethod
    def from_dict(cls, d: Optional[Dict[str, Any]]) -> Optional["MidViewDTO"]:
        """从 dict（LLM 输出或 DB JSONB 列）构造；None/空 → None（向后兼容）。"""
        if not isinstance(d, dict) or not d:
            return None
        try:
            ts = int(d.get("timing_score") or 0)
        except (TypeError, ValueError):
            ts = 0
        ts = max(0, min(100, ts))  # clamp 到 0-100（DTO 契约）
        try:
            return cls(
                direction=str(d.get("direction") or "neutral").lower(),
                timing_score=ts,
                timing_rationale=str(d.get("timing_rationale") or ""),
                key_levels=d.get("key_levels") if isinstance(d.get("key_levels"), dict) else None,
                invalidation_for_timing=str(d.get("invalidation_for_timing") or ""),
                updated_at=float(d.get("updated_at") or 0.0),
            )
        except Exception:
            return None


@dataclass
class ThesisDTO:
    thesis_id: str
    session_id: str
    symbol: str
    tier: str
    direction: str = "neutral"
    thesis_summary: str = ""
    reasoning_content: str = ""   # [add] reasoning 模型完整思维链（供复盘/学习）
    llm_conviction: int = 0
    hub_composite: float = 0.0
    hub_adjusted: float = 0.0
    consistency: float = 0.0
    open_readiness: int = 0
    stable_since: Optional[datetime] = None
    direction_history: List[str] = field(default_factory=list)  # [fix] 最近N次LLM方向，用于多数票stable判定
    review_count: int = 0
    tranche_stage: int = 0
    # [阶段3b] LLM 研判是否建议开仓（open_gate 风险底线之一）。
    # None=LLM 未明确给出（向后兼容，按 AI should_open/hub 默认放行）；
    # False=LLM 明确说不建议开仓 → open_gate 拦截。
    recommend_open: Optional[bool] = None
    # [Phase A 修复 Bug2] LLM 明确判定 thesis 已完全失效 → 触发主动 close。
    # 与价格类 invalidation 互补：叙事类 invalidation(无 price/operator)原来
    # 永远不会自动平仓，现在 LLM 在 thesis_update 里输出 should_close=true 即触发。
    should_close: bool = False
    regime_hash: str = ""
    invalidation: Dict[str, Any] = field(default_factory=dict)
    missing_evidence: List[str] = field(default_factory=list)
    owm_weights: Dict[str, float] = field(default_factory=dict)
    # [阶段2] 中周期子视图（仅长线 thesis 使用；None=向后兼容退化为现状）。
    mid_view: Optional[MidViewDTO] = None
    # [2026-08-05 v6 6.3 第3项] LLM exit_plan 止损参数直通：开仓优先用
    # thesis.sl_pct/tp_pct（LLM 提供，ATR 下限硬校验），structure_stops 降级兜底。
    # 0.0 = LLM 本轮未提供（向后兼容：走 structure_stops 兜底）。
    sl_pct: float = 0.0
    tp_pct: float = 0.0
    # [v6 阶段2 S2-7] regime 参数建议通道：校验后的 applied 档位
    #（regime/sl_multiplier/tp_trigger/trailing/addon_rhythm，None=未提供）
    regime_suggestion: Optional[Dict[str, Any]] = None
    # [v6 4.2] 本次决策注入的回测智慧 id 列表（qual prompt 注入时解析标记写入，
    # 平仓结算时据此评估智慧效果）。空 = 本轮未注入。
    wisdom_ids: List[int] = field(default_factory=list)
    updated_at: Optional[datetime] = None

    def to_dict(self) -> Dict[str, Any]:
        return {
            "thesis_id": self.thesis_id,
            "session_id": self.session_id,
            "symbol": self.symbol,
            "tier": self.tier,
            "direction": self.direction,
            "thesis_summary": self.thesis_summary,
            "reasoning_content": self.reasoning_content,
            "llm_conviction": self.llm_conviction,
            "hub_composite": self.hub_composite,
            "hub_adjusted": self.hub_adjusted,
            "consistency": self.consistency,
            "open_readiness": self.open_readiness,
            "stable_since": self.stable_since.isoformat() if self.stable_since else None,
            "review_count": self.review_count,
            "tranche_stage": self.tranche_stage,
            "recommend_open": self.recommend_open,
            "should_close": self.should_close,
            "regime_hash": self.regime_hash,
            "invalidation": self.invalidation,
            "missing_evidence": self.missing_evidence,
            "owm_weights": self.owm_weights,
            "mid_view": self.mid_view.to_dict() if self.mid_view else None,
            "sl_pct": self.sl_pct,
            "tp_pct": self.tp_pct,
            "regime_suggestion": self.regime_suggestion,
            "wisdom_ids": self.wisdom_ids,
            "updated_at": self.updated_at.isoformat() if self.updated_at else None,
        }


@dataclass
class QualUpdateResult:
    direction: str = "neutral"
    conviction_delta: int = 0
    thesis_summary: str = ""
    reasoning_content: str = ""   # [add] 从 agent _reasoning_content 透传的思维链
    cited_event_ids: List[str] = field(default_factory=list)
    missing_evidence: List[str] = field(default_factory=list)
    invalidation: Dict[str, Any] = field(default_factory=dict)
    recommend_open: Optional[bool] = None
    # [Phase A 修复 Bug2] LLM 判定 thesis 完全失效应主动离场（叙事类 invalidation
    # 原来永远不触发 close，现由 LLM 输出 should_close=true 驱动）。
    should_close: bool = False
    debate_signal: Optional[float] = None
    # [阶段2] LLM 输出的中周期视图（解析后的 dict；None=LLM 未返回，向后兼容）。
    mid_view: Optional[Dict[str, Any]] = None
    # [2026-08-05 v6 6.3 第3项] LLM exit_plan 止损参数直通（0.0=未提供）。
    sl_pct: float = 0.0
    tp_pct: float = 0.0
    # [v6 阶段2 S2-7] LLM regime 判定 + 参数档位建议（解析+规则校验后，None=未提供）
    regime_suggestion: Optional[Dict[str, Any]] = None
    raw: Dict[str, Any] = field(default_factory=dict)


@dataclass
class PerceptionPacket:
    symbol: str
    tier: str
    session_id: str
    ts: float
    price: float
    market_summary_sym: Dict[str, Any]
    orchestrator: Dict[str, Any]
    quant_brief: Dict[str, Any]
    analyst_reports: Dict[str, Any]
    pre_screener_passed: bool = True
    pre_screener_reason: str = ""
    regime_hash: str = ""
    slot_action: str = ""
    portfolio: Dict[str, Any] = field(default_factory=dict)
    trading_mode: str = "paper"
    account_id: Optional[int] = None


@dataclass
class MltoTickResult:
    # action: buy | sell | hold | close
    # [阶段3e] close: invalidation 触发的主动离场(仅在有持仓时发出)。
    #   价格类 invalidation 由 orchestrator._invalidation_triggered 自动判定;
    #   叙事类 invalidation 由 LLM 在 thesis_update 中复评后改写 direction/conviction。
    action: str
    reason: str
    thesis: Optional[ThesisDTO] = None
    hub: Optional[HubDecision] = None
    tranche_margin_pct: float = 0.0
    memory_event_ids: List[str] = field(default_factory=list)
    sl_pct: float = 0.0
    tp_pct: float = 0.0
    confidence: int = 0
