"""AI 决策入口与调用路径注册表。

本模块梳理全自动交易系统中所有 AI 决策入口，并标注默认是否激活、
下游执行链路与已知旁路风险。供运维审计与架构冻结阶段使用。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Dict, List, Optional


class PipelineStage(str, Enum):
    """目标架构五层 — 所有开仓必须依次经过（风控层可拒绝/缩减）。"""

    DIRECTION = "direction"
    SIZING = "sizing"
    RISK = "risk"
    EXECUTION = "execution"
    FEEDBACK = "feedback"


@dataclass(frozen=True)
class DecisionEntryPoint:
    """单个 AI 决策入口描述。"""

    id: str
    name: str
    module: str
    method: str
    active_by_default: bool
    trigger: str
    downstream: List[str]
    bypass_risks: List[str] = field(default_factory=list)
    notes: str = ""


# ── 入口注册表（按 plan 梳理）──────────────────────────────────────────────
ENTRY_POINTS: List[DecisionEntryPoint] = [
    DecisionEntryPoint(
        id="unified_loop_ai_first",
        name="统一循环 · ai_first",
        module="full_auto_trading_service",
        method="_run_unified_loop → _run_trading_cycle",
        active_by_default=True,
        trigger="FULLAUTO_FLOW_MODE=ai_first（默认），每 90s tick",
        downstream=[
            "_collect_market_snapshot",
            "_run_analyst_system",
            "_run_analyst_system_unified",
            "analyst_system.run_full_analysis",
            "DualAgentCoordinator.coordinate | MasterController.synthesize",
            "_execute_master_decisions",
            "PositionSizingAgent.build_plan",
            "UnifiedRiskGate / master_close_guard",
            "paper_trading_engine",
        ],
        notes="生产默认主路径",
    ),
    DecisionEntryPoint(
        id="unified_loop_qaa_v3",
        name="统一循环 · QAA v3",
        module="full_auto_trading_service",
        method="_run_unified_loop → _run_qaa_v3_tick → _run_analyst_system_v3",
        active_by_default=False,
        trigger="QAA_MODE=qaa 且 QAA_V3_ENABLED=true",
        downstream=[
            "QAAContext TickOrchestrator",
            "_run_analyst_system_v3",
            "analyst_system.run_full_analysis",
            "_execute_master_decisions",
            "PositionSizingAgent.build_plan",
            "paper_trading_engine",
        ],
        bypass_risks=["QAA 未就绪时回退 _run_trading_cycle（与 ai_first 相同）"],
        notes="v3 路径用短生命周期 DB session，LLM 阶段不持锁",
    ),
    DecisionEntryPoint(
        id="unified_loop_qaa_legacy",
        name="统一循环 · QAA legacy",
        module="full_auto_trading_service",
        method="_run_unified_loop → _run_qaa_tick",
        active_by_default=False,
        trigger="QAA_MODE=qaa 且 QAA_V3_ENABLED=false",
        downstream=[
            "event_bus 多 Agent handler",
            "_qaa_master_controller（规则化快评）",
            "_run_analyst_system（hybrid 兜底 LLM）",
            "_execute_master_decisions",
        ],
        bypass_risks=["快 tick 可能先走规则化 _qaa_master_controller，再 hybrid 调 LLM"],
    ),
    DecisionEntryPoint(
        id="analyst_legacy_tier_parallel",
        name="三 tier 并行（legacy）",
        module="full_auto_trading_service",
        method="_run_analyst_system → tier_executor.execute_parallel_tiers",
        active_by_default=False,
        trigger="FULLAUTO_AI_UNIFIED_ANALYSIS=false",
        downstream=[
            "TierParallelExecutor ×3（每 tier 独立全套分析师+LLM）",
            "multi_timeframe_orchestrator 协调",
            "_execute_master_decisions",
        ],
        bypass_risks=[
            "每 tier 独立 LLM，决策可能冲突",
            "historically 硬编码 leverage=15（已改为动态）",
        ],
        notes="降级时仍走 _run_analyst_system_unified",
    ),
    DecisionEntryPoint(
        id="analyst_fallback_legacy_execute",
        name="分析师异常回退",
        module="full_auto_trading_service",
        method="_run_analyst_system_unified except → _execute_ai_decisions",
        active_by_default=False,
        trigger="FULLAUTO_ANALYST_FALLBACK=legacy 且分析师抛异常",
        downstream=[
            "call_ai_for_decision（逐策略 LLM）",
            "PositionSizingAgent.build_plan",
            "paper_trading_engine",
        ],
        bypass_risks=["绕过 MasterController 与五路分析师综合"],
    ),
    DecisionEntryPoint(
        id="hold_timeout_review",
        name="持仓时限复审",
        module="full_auto_trading_service",
        method="_run_hold_timeout_ai_review_if_needed",
        active_by_default=True,
        trigger="持仓到达 tier 复审点，独立于主 tick",
        downstream=["analyst_system.run_full_analysis", "_execute_master_decisions"],
        notes="仅管理已有仓，不开新方向研究",
    ),
    DecisionEntryPoint(
        id="dual_agent_primary",
        name="DualAgent 主路径",
        module="dual_agent_coordinator",
        method="coordinate",
        active_by_default=True,
        trigger="DUAL_AGENT_MODE=primary（模拟盘默认）|advisory|shadow",
        downstream=[
            "DirectionAgent.decide",
            "TradeRiskAgent.review",
            "PositionSizingAgent.build_plan",
            "MasterController.synthesize（仅 shadow/advisory 时并行）",
        ],
        notes="primary=Direction+Risk 直接决策；模拟盘无需 shadow 对比",
    ),
]


def get_active_entry_points() -> List[DecisionEntryPoint]:
    """根据当前 settings 返回实际会走到的入口。"""
    from backend.config.settings import (
        DUAL_AGENT_MODE,
        FULLAUTO_AI_UNIFIED_ANALYSIS,
        FULLAUTO_ANALYST_FALLBACK,
        FULLAUTO_FLOW_MODE,
        QAA_MODE,
        QAA_V3_ENABLED,
    )

    active: List[DecisionEntryPoint] = []
    if QAA_MODE == "qaa":
        if QAA_V3_ENABLED:
            active.append(_by_id("unified_loop_qaa_v3"))
        else:
            active.append(_by_id("unified_loop_qaa_legacy"))
    elif FULLAUTO_FLOW_MODE == "ai_first":
        active.append(_by_id("unified_loop_ai_first"))

    if not FULLAUTO_AI_UNIFIED_ANALYSIS:
        active.append(_by_id("analyst_legacy_tier_parallel"))

    if FULLAUTO_ANALYST_FALLBACK == "legacy":
        active.append(_by_id("analyst_fallback_legacy_execute"))

    if (DUAL_AGENT_MODE or "primary").lower() in ("shadow", "advisory", "primary"):
        active.append(_by_id("dual_agent_primary"))

    active.append(_by_id("hold_timeout_review"))
    return active


def get_canonical_pipeline() -> List[PipelineStage]:
    """冻结架构：所有开仓应经过的标准链路。"""
    return [
        PipelineStage.DIRECTION,
        PipelineStage.SIZING,
        PipelineStage.RISK,
        PipelineStage.EXECUTION,
        PipelineStage.FEEDBACK,
    ]


def render_entrypoint_report() -> str:
    """生成可读的入口路径报告（Markdown）。"""
    lines = ["# AI 决策入口与调用路径", ""]
    lines.append("## 当前激活入口")
    for ep in get_active_entry_points():
        lines.append(f"- **{ep.name}** (`{ep.id}`)")
        lines.append(f"  - 触发: {ep.trigger}")
        lines.append(f"  - 调用: `{ep.module}.{ep.method}`")
        if ep.bypass_risks:
            lines.append(f"  - ⚠️ 旁路风险: {'; '.join(ep.bypass_risks)}")
    lines.append("")
    lines.append("## 标准执行链路（冻结）")
    lines.append("```")
    lines.append(
        "MarketData → Direction(Master/Dual) → PositionSizingAgent → "
        "RiskGate → ExecutionEngine → DecisionRetrospective → PromptFeedback"
    )
    lines.append("```")
    lines.append("")
    lines.append("## 全部注册入口")
    for ep in ENTRY_POINTS:
        status = "✅ 默认激活" if ep.active_by_default else "⏸ 条件激活"
        lines.append(f"### {ep.name} [{status}]")
        lines.append(f"- ID: `{ep.id}`")
        lines.append(f"- 模块: `{ep.module}.{ep.method}`")
        lines.append(f"- 触发: {ep.trigger}")
        lines.append("- 下游:")
        for step in ep.downstream:
            lines.append(f"  - {step}")
        if ep.bypass_risks:
            lines.append("- 旁路风险:")
            for risk in ep.bypass_risks:
                lines.append(f"  - {risk}")
        if ep.notes:
            lines.append(f"- 备注: {ep.notes}")
        lines.append("")
    return "\n".join(lines)


def _by_id(entry_id: str) -> DecisionEntryPoint:
    for ep in ENTRY_POINTS:
        if ep.id == entry_id:
            return ep
    raise KeyError(entry_id)


def audit_decision_sizing_fields(decision: Dict) -> Optional[str]:
    """执行前审计：开仓决策是否携带 sizing 规划字段。"""
    action = str(decision.get("action") or "hold").lower()
    if action not in ("buy", "sell", "pyramid", "dca"):
        return None
    if not decision.get("_respect_sizing_plan") and not decision.get("_sizing_source"):
        return (
            f"{decision.get('symbol')}: 开仓动作缺少 PositionSizingAgent 审计字段 "
            "(_respect_sizing_plan / _sizing_source)"
        )
    return None
