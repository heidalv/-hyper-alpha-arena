"""TradeProposal — 统一交易提案协议（Proposer → Evaluator → Executor）。"""

from __future__ import annotations

import time
import uuid
from dataclasses import asdict, dataclass, field
from typing import Any, Dict, Optional


@dataclass
class TradeProposal:
    symbol: str
    tier: str
    trade_nature: str
    action: str
    confidence: float
    sl_pct: float = 0.0
    tp_pct: float = 0.0
    source_lane: str = "unknown"
    reasoning: str = ""
    proposal_id: str = field(default_factory=lambda: str(uuid.uuid4()))
    trace_id: str = ""
    market_snapshot_ref: str = ""
    created_at: float = field(default_factory=time.time)
    extra: Dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        self.symbol = str(self.symbol or "").upper()
        self.tier = (self.tier or "mid").lower()
        self.trade_nature = (self.trade_nature or "swing").lower()
        self.action = (self.action or "hold").lower()
        from backend.services.decision_core.threshold_resolver import normalize_confidence_pct
        self.confidence = normalize_confidence_pct(self.confidence)

    @classmethod
    def from_agent(
        cls,
        *,
        sym: str,
        tier: str,
        action: str,
        confidence: int | float,
        trade_nature: str,
        sl_pct: float = 0.0,
        tp_pct: float = 0.0,
        source_lane: str = "swing_independent",
        reasoning: str = "",
        trace_id: str = "",
        **extra: Any,
    ) -> "TradeProposal":
        return cls(
            symbol=sym,
            tier=tier,
            trade_nature=trade_nature,
            action=action,
            confidence=float(confidence or 0),
            sl_pct=float(sl_pct or 0),
            tp_pct=float(tp_pct or 0),
            source_lane=source_lane,
            reasoning=reasoning or "",
            trace_id=trace_id or "",
            extra=dict(extra),
        )

    def to_decision_dict(self) -> Dict[str, Any]:
        """转为 evaluate_open_decision / paper_engine 使用的 dec dict。"""
        d: Dict[str, Any] = {
            "action": self.action,
            "operation": self.action,
            "symbol": self.symbol,
            "confidence": int(self.confidence),
            "confidence_pct": float(self.confidence),
            "timeframe_tier": self.tier,
            "tier": self.tier,
            "trade_nature": self.trade_nature,
            "stop_loss_pct": self.sl_pct,
            "take_profit_pct": self.tp_pct,
            "_agent_independent": True,
            "_proposal_id": self.proposal_id,
            "_source_lane": self.source_lane,
            "reasoning": self.reasoning,
        }
        if self.trace_id:
            d["trace_id"] = self.trace_id
        d.update(self.extra)
        return d

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)
