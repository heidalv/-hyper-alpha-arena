"""evaluate_proposal — Proposal → evaluate → verdict 统一入口。"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any, Dict, Optional, Tuple

logger = logging.getLogger(__name__)


@dataclass
class EvaluateVerdict:
    allowed: bool
    reason: str
    adjustments: Dict[str, Any] = field(default_factory=dict)
    layer: str = "1"
    rule: str = ""
    data_contract_ok: bool = True

    def to_dict(self) -> dict:
        return {
            "allowed": self.allowed,
            "reason": self.reason,
            "code_reason": self.reason,
            "adjustments": self.adjustments,
            "layer": self.layer,
            "rule": self.rule,
            "data_contract_ok": self.data_contract_ok,
        }


def evaluate_proposal(
    *,
    db,
    account_id: int,
    proposal,
    market_data: Optional[dict],
    mode: str = "paper",
    persistence_allow: bool = True,
) -> EvaluateVerdict:
    """按 tier 路由 evaluate；short 暂交 evaluate_open_decision。"""
    from backend.services.decision_core.data_contract import apply_data_contract_gate
    from backend.services.constitutional_profile import get_profile

    tier = (proposal.tier or "mid").lower()
    profile = get_profile(mode)

    dc_ok, dc_reason = apply_data_contract_gate(tier, market_data, mode=mode)
    if not dc_ok:
        return EvaluateVerdict(
            allowed=False,
            reason=dc_reason,
            layer="data",
            rule="strict_data_contract",
            data_contract_ok=False,
        )

    dec = proposal.to_decision_dict()
    mkt = market_data if isinstance(market_data, dict) else {}

    if tier in ("short", "scalp"):
        from backend.services.decision_core.pipeline import evaluate_open_decision
        allowed, reason, adjustments = evaluate_open_decision(
            db=db,
            account_id=account_id,
            symbol=proposal.symbol,
            dec=dec,
            market_data=mkt,
            mode=mode,
        )
    else:
        from backend.services.decision_core.pipeline import evaluate_midlong_open
        allowed, reason, adjustments = evaluate_midlong_open(
            db=db,
            account_id=account_id,
            symbol=proposal.symbol,
            dec=dec,
            market_data=mkt,
            mode=mode,
            persistence_allow=persistence_allow,
        )

    if not allowed and profile.allows_paper_probe():
        pass  # Probe 已在 evaluate_midlong_open 内处理

    rule = ""
    layer = "1"
    if reason.startswith("[StrictData]"):
        layer = "data"
    elif "V5Gate" in reason or "confidence" in reason.lower():
        layer = "1"
    elif reason.startswith("[DCP]"):
        layer = "2"

    return EvaluateVerdict(
        allowed=allowed,
        reason=reason or "",
        adjustments=dict(adjustments or {}),
        layer=layer,
        rule=rule,
    )


def evaluate_scalp_proposal(
    *,
    db,
    account_id: int,
    proposal,
    market_data: Optional[dict],
    gate_allowed: bool,
    gate_reason: str = "",
    gate_tier: str = "",
    lane_decision_id: str = "",
    mode: str = "paper",
) -> EvaluateVerdict:
    """Scalp 专用：ScalpExecutionGate 结果 + V5 evaluate_open_decision 叠层。"""
    if not gate_allowed:
        return EvaluateVerdict(
            allowed=False,
            reason=gate_reason or "scalp_gate_block",
            layer="scalp_gate",
            rule=gate_tier or "scalp_execution_gate",
        )

    from backend.services.decision_core.data_contract import apply_data_contract_gate
    dc_ok, dc_reason = apply_data_contract_gate("short", market_data, mode=mode)
    if not dc_ok:
        return EvaluateVerdict(
            allowed=False,
            reason=dc_reason,
            layer="data",
            rule="strict_data_contract",
        )

    dec = proposal.to_decision_dict()
    dec["_lane_decision_id"] = lane_decision_id
    from backend.services.decision_core.pipeline import evaluate_open_decision
    allowed, reason, adjustments = evaluate_open_decision(
        db=db,
        account_id=account_id,
        symbol=proposal.symbol,
        dec=dec,
        market_data=market_data if isinstance(market_data, dict) else {},
        mode=mode,
    )
    return EvaluateVerdict(
        allowed=allowed,
        reason=reason or "",
        adjustments=dict(adjustments or {}),
        layer="1" if allowed else "scalp_v5",
        rule=gate_tier,
    )
