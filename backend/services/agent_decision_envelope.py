"""AgentDecisionEnvelope — 中线/长线 Agent 决策归因 envelope（开仓→平仓贯通）。"""
from __future__ import annotations

import uuid
from dataclasses import asdict, dataclass, field
from typing import Any, Dict, List, Optional


@dataclass
class AgentDecisionEnvelope:
    agent_source: str = ""  # swing_agent | trend_agent
    lane_decision_id: str = ""
    alignment_score: int = 0
    cited_fact_ids: List[str] = field(default_factory=list)
    evidence_available_ratio: float = 0.0
    structure_sl_price: float = 0.0
    structure_tp_price: float = 0.0
    sl_pct: float = 0.0
    tp_pct: float = 0.0
    sl_source: str = ""
    quant_brief: Dict[str, Any] = field(default_factory=dict)
    orch_snapshot_ts: float = 0.0
    # MLTO fields
    thesis_id: str = ""
    hub_composite: float = 0.0
    hub_adjusted: float = 0.0
    consistency: float = 0.0
    open_readiness: int = 0
    memory_event_ids: List[str] = field(default_factory=list)
    tranche_stage: int = 0
    debate_log_id: str = ""
    evidence_chain_snapshot: List[Dict[str, Any]] = field(default_factory=list)
    open_readiness_at_entry: int = 0
    regime_hash: str = ""

    @classmethod
    def new(cls, agent_source: str, **kwargs) -> "AgentDecisionEnvelope":
        env = cls(agent_source=agent_source, lane_decision_id=str(uuid.uuid4()), **kwargs)
        return env

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: Optional[Dict[str, Any]]) -> Optional["AgentDecisionEnvelope"]:
        if not isinstance(data, dict) or not data.get("agent_source"):
            return None
        known = {f.name for f in cls.__dataclass_fields__.values()}
        return cls(**{k: data[k] for k in known if k in data})

    def attach_to_dec(self, dec: Dict[str, Any]) -> None:
        dec["_agent_envelope"] = self.to_dict()
        dec["_decision_source"] = self.agent_source
        if self.sl_pct > 0:
            dec["stop_loss_pct"] = self.sl_pct
        if self.tp_pct > 0:
            dec["take_profit_pct"] = self.tp_pct
