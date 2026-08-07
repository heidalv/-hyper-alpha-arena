"""Direction / Sizing / Risk / Execution / Feedback 唯一职责边界。

定义各层允许输出的字段、禁止行为，以及决策审计结构。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Dict, List, Optional, Set


class AgentRole(str, Enum):
    DIRECTION = "direction"
    SIZING = "sizing"
    RISK = "risk"
    EXECUTION = "execution"
    FEEDBACK = "feedback"


# 各层允许输出的 action 集合
DIRECTION_ACTIONS: Set[str] = {"buy", "sell", "hold", "pyramid", "dca"}
RISK_ACTIONS: Set[str] = {"hold", "reduce", "close", "adjust_sl", "adjust_tp"}
EXECUTION_ACTIONS: Set[str] = {"buy", "sell", "reduce", "close", "pyramid", "dca"}

# 各层拥有权的字段
FIELD_OWNERSHIP: Dict[str, AgentRole] = {
    "action": AgentRole.DIRECTION,  # 方向层提议；风控层可覆盖为 hold/reduce/close
    "confidence": AgentRole.DIRECTION,
    "reasoning": AgentRole.DIRECTION,
    "trade_nature": AgentRole.DIRECTION,
    "expected_hold_hours": AgentRole.DIRECTION,
    "stop_loss_pct": AgentRole.DIRECTION,
    "take_profit_pct": AgentRole.DIRECTION,
    "risk_reward_ratio": AgentRole.DIRECTION,
    "leverage": AgentRole.SIZING,
    "position_pct": AgentRole.SIZING,
    "_sizing_notional_usd": AgentRole.SIZING,
    "_sizing_margin_usd": AgentRole.SIZING,
    "_sizing_max_loss_usd": AgentRole.SIZING,
    "_sizing_source": AgentRole.SIZING,
    "_respect_sizing_plan": AgentRole.SIZING,
    "size_multiplier": AgentRole.RISK,
    "leverage_cap": AgentRole.RISK,
    "adjust_tp": AgentRole.RISK,
    "adjust_sl": AgentRole.RISK,
    "partial_close_pct": AgentRole.RISK,
    "extend_hold_hours": AgentRole.RISK,
}


@dataclass
class AgentContract:
    role: AgentRole
    may_decide: List[str]
    must_not: List[str]
    output_fields: List[str]
    notes: str = ""


AGENT_CONTRACTS: List[AgentContract] = [
    AgentContract(
        role=AgentRole.DIRECTION,
        may_decide=["方向", "trade_nature", "置信度", "入场理由", "预期持仓时长"],
        must_not=["决定 leverage/position_pct", "主动 close 有 SL 的仓位", "放大仓位"],
        output_fields=[
            "symbol", "action", "confidence", "reasoning", "trade_nature",
            "expected_hold_hours", "stop_loss_pct", "take_profit_pct", "risk_reward_ratio",
        ],
        notes="DirectionAgent / MasterController 方向段",
    ),
    AgentContract(
        role=AgentRole.SIZING,
        may_decide=["leverage", "position_pct", "notional_usd", "margin_usd", "max_loss_usd"],
        must_not=["改变方向", "放大超过风险预算", "绕过 tier cap"],
        output_fields=[
            "leverage", "position_pct", "_sizing_notional_usd", "_sizing_margin_usd",
            "_sizing_max_loss_usd", "_sizing_source", "_respect_sizing_plan",
        ],
        notes="PositionSizingAgent 为唯一 sizing 源",
    ),
    AgentContract(
        role=AgentRole.RISK,
        may_decide=["拒绝开仓", "降杠杆", "缩仓", "收紧 SL", "限时 extend"],
        must_not=["放大仓位", "提高 leverage", "增加 position_pct"],
        output_fields=[
            "action(override)", "size_multiplier", "leverage_cap",
            "adjust_sl", "adjust_tp", "partial_close_pct", "extend_hold_hours",
        ],
        notes="TradeRiskAgent / UnifiedRiskGate / master_close_guard",
    ),
    AgentContract(
        role=AgentRole.EXECUTION,
        may_decide=["订单价格", "数量", "TP/SL 落地", "手续费估算"],
        must_not=["重新发明策略方向", "独立修改 sizing 比例"],
        output_fields=["order_id", "fill_price", "filled_size", "fee"],
        notes="paper_trading_engine / position_memory_manager（保真模式）",
    ),
    AgentContract(
        role=AgentRole.FEEDBACK,
        may_decide=["复盘标签", "教训提炼", "策略门槛调整建议"],
        must_not=["直接下单", "覆盖当轮决策"],
        output_fields=[
            "was_correct", "mistake_analysis", "lesson_learned", "policy_adjustments",
        ],
        notes="DecisionRetrospective → decision_feedback_service → 下轮 prompt",
    ),
]


@dataclass
class DecisionAuditTrail:
    """单笔决策全链路审计记录。"""

    symbol: str
    direction_source: str = "master"
    sizing_source: str = ""
    risk_modifications: List[str] = field(default_factory=list)
    final_action: str = "hold"
    final_leverage: int = 0
    final_position_pct: float = 0.0
    final_notional_usd: float = 0.0
    deviation_reasons: List[str] = field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "symbol": self.symbol,
            "direction_source": self.direction_source,
            "sizing_source": self.sizing_source,
            "risk_modifications": self.risk_modifications,
            "final_action": self.final_action,
            "final_leverage": self.final_leverage,
            "final_position_pct": self.final_position_pct,
            "final_notional_usd": self.final_notional_usd,
            "deviation_reasons": self.deviation_reasons,
        }


def validate_risk_cannot_amplify(decision: Dict[str, Any], prior: Dict[str, Any]) -> Optional[str]:
    """风控层不得放大仓位或杠杆。"""
    prior_pct = float(prior.get("position_pct") or prior.get("_sizing_margin_usd") or 0)
    new_pct = float(decision.get("position_pct") or 0)
    if prior_pct > 0 and new_pct > prior_pct * 1.001:
        return f"Risk 层试图放大 position_pct {prior_pct:.4f} → {new_pct:.4f}"

    prior_lev = int(prior.get("leverage") or 0)
    new_lev = int(decision.get("leverage") or 0)
    if prior_lev > 0 and new_lev > prior_lev:
        return f"Risk 层试图提高 leverage {prior_lev} → {new_lev}"
    return None


def build_audit_trail(
    decision: Dict[str, Any],
    *,
    direction_source: str = "master",
    risk_mods: Optional[List[str]] = None,
) -> DecisionAuditTrail:
    return DecisionAuditTrail(
        symbol=str(decision.get("symbol") or ""),
        direction_source=direction_source,
        sizing_source=str(decision.get("_sizing_source") or "unknown"),
        risk_modifications=list(risk_mods or []),
        final_action=str(decision.get("action") or "hold"),
        final_leverage=int(decision.get("leverage") or 0),
        final_position_pct=float(decision.get("position_pct") or 0),
        final_notional_usd=float(decision.get("_sizing_notional_usd") or 0),
        deviation_reasons=list(decision.get("_sizing_reasons") or []),
    )


def render_contracts_markdown() -> str:
    lines = ["# Agent 职责边界契约", ""]
    for c in AGENT_CONTRACTS:
        lines.append(f"## {c.role.value.upper()}")
        lines.append(f"- 可决定: {', '.join(c.may_decide)}")
        lines.append(f"- 禁止: {', '.join(c.must_not)}")
        lines.append(f"- 输出字段: `{', '.join(c.output_fields)}`")
        if c.notes:
            lines.append(f"- 备注: {c.notes}")
        lines.append("")
    lines.append("## 标准流水线")
    lines.append("```")
    lines.append("Direction → Sizing → Risk → Execution → Feedback")
    lines.append("```")
    return "\n".join(lines)
