"""Agent Fact Guard — L3 事实校验（off / shadow / enforce）。"""
from __future__ import annotations

import logging
import os
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

from backend.services.agent_evidence_builder import AgentEvidenceFact

logger = logging.getLogger(__name__)


def get_fact_guard_mode() -> str:
    try:
        from backend.config.settings import AGENT_FACT_GUARD_MODE
        return (AGENT_FACT_GUARD_MODE or "shadow").lower()
    except Exception:
        return os.getenv("AGENT_FACT_GUARD_MODE", "shadow").lower()


@dataclass
class FactGuardResult:
    allow: bool
    violations: List[str] = field(default_factory=list)
    penalty: int = 0
    mode: str = "shadow"
    adjusted_confidence: Optional[int] = None

    def to_dict(self) -> Dict[str, Any]:
        return {
            "allow": self.allow,
            "violations": list(self.violations),
            "penalty": self.penalty,
            "mode": self.mode,
            "adjusted_confidence": self.adjusted_confidence,
        }


def verify_agent_decision(
    *,
    action: str,
    confidence: float,
    reasoning: str,
    cited_fact_ids: List[str],
    facts: List[AgentEvidenceFact],
    agent_type: str,
    min_confidence: int = 55,
    force_enforce: bool = False,
) -> FactGuardResult:
    """校验 LLM 决策与证据清单一致性。

    force_enforce=True 时把 shadow/off 强制提升为 enforce（S2-3：中长线 paper 试单
    严格模式下让 FactGuard 真正拦截幻觉/无据决策，而非仅记录）。
    """
    mode = get_fact_guard_mode()
    if mode == "shadow":
        try:
            from backend.config.settings import AGENT_FACT_GUARD_PAPER_ENFORCE
            if AGENT_FACT_GUARD_PAPER_ENFORCE:
                mode = "enforce"
        except Exception:
            pass
    if force_enforce and mode != "enforce":
        mode = "enforce"
    if mode == "off":
        return FactGuardResult(allow=True, mode="off")

    fact_map = {f.id: f for f in facts}
    violations: List[str] = []
    penalty = 0
    allow = True
    adj_conf = int(confidence or 0)
    action_l = (action or "hold").lower()
    reasoning_text = reasoning or ""

    for fid in cited_fact_ids or []:
        fact = fact_map.get(fid)
        if fact is None or not fact.available:
            violations.append(f"FG_MISSING_DATA:{fid}")

    rsi_fact = fact_map.get("rsi_1h")
    if rsi_fact and rsi_fact.available:
        try:
            rsi = float(rsi_fact.value)
            if any(k in reasoning_text for k in ("超卖", "RSI低", "rsi低")) and rsi >= 35:
                violations.append("FG_RSI_OVERSOLD")
                penalty += 15
            if "超买" in reasoning_text and rsi <= 65:
                violations.append("FG_RSI_OVERBOUGHT")
                penalty += 15
        except (TypeError, ValueError):
            pass

    if "共振" in reasoning_text:
        ema_1h = fact_map.get("ema_trend_1h")
        mid_bias = fact_map.get("mid_bias")
        if ema_1h and ema_1h.available and mid_bias and mid_bias.available:
            ema_bull = ema_1h.value == "bullish"
            ema_bear = ema_1h.value == "bearish"
            mid_bull = mid_bias.value == "bullish"
            mid_bear = mid_bias.value == "bearish"
            if (ema_bull and mid_bear) or (ema_bear and mid_bull):
                violations.append("FG_MULTI_TF_ALIGN")

    if action_l in ("buy", "sell"):
        macro = fact_map.get("macro_cycle_phase")
        if macro and macro.available and macro.value == "decline" and action_l == "buy":
            try:
                from backend.services.macro_regime_service import macro_regime_service
                conf = float(macro_regime_service.get_state("GLOBAL").phase_confidence or 0)
                if conf >= 0.6:
                    violations.append("FG_MACRO_DECLINE_LONG")
            except Exception:
                pass

    if violations:
        logger.info(
            "[FactGuard:%s] mode=%s action=%s violations=%s agent=%s",
            agent_type, mode, action_l, violations, agent_type,
        )

    if mode == "enforce":
        if any(v.startswith("FG_MISSING_DATA") for v in violations):
            allow = False
        if "FG_MULTI_TF_ALIGN" in violations or "FG_MACRO_DECLINE_LONG" in violations:
            allow = False
        if penalty:
            adj_conf = max(0, adj_conf - penalty)
        if action_l in ("buy", "sell") and adj_conf < min_confidence:
            allow = False
    else:
        allow = True

    return FactGuardResult(
        allow=allow,
        violations=violations,
        penalty=penalty,
        mode=mode,
        adjusted_confidence=adj_conf if penalty else None,
    )


def build_evidence_audit(
    facts: List[AgentEvidenceFact],
    cited_fact_ids: List[str],
    fg: FactGuardResult,
) -> Dict[str, Any]:
    from backend.services.agent_evidence_builder import facts_to_audit_payload
    return {
        "evidence_checklist": facts_to_audit_payload(facts),
        "cited_facts": list(cited_fact_ids or []),
        "fact_guard": fg.to_dict(),
    }
