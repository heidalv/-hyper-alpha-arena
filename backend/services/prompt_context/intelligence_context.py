"""
Intelligence context builder — news, whale tracking, funding rate, OI,
sentiment, orchestrator summary.
"""
from __future__ import annotations

from typing import Any, Dict

from .types import BuildInput, BuildResult


def _build_intelligence_context(inp: BuildInput) -> BuildResult:
    """Build intelligence-level context.

    Consumes: inp.account, inp.ordered_symbols, inp.trigger_context,
              inp.hyperliquid_state.
    """
    result: Dict[str, Any] = {}

    # Orchestrator directions from trigger context
    trigger = inp.trigger_context or {}
    orch_dirs = trigger.get("orchestrator_directions", {})
    orch_summary = _format_orchestrator(orch_dirs)

    # 积分套利上下文（与 rebate tick 共用，注入 Master / legacy prompt）
    try:
        from backend.services.rebate_arb.tick_context import get_last_rebate_arb_context

        rebate_ctx = get_last_rebate_arb_context()
        rebate_text = (rebate_ctx.get("summary_text") or "").strip()
        if rebate_text:
            orch_summary = f"{orch_summary}\n\n{rebate_text}"
    except Exception:
        pass

    result["strategy_orchestrator_summary"] = orch_summary

    # Intelligence signal placeholder (filled by call_ai_for_decision)
    result["intelligence_signal"] = trigger.get("intelligence_signal", "N/A")

    # Trigger context explanation
    result["trigger_context"] = _format_trigger_context(trigger)

    return result


def _format_orchestrator(dirs: Dict) -> str:
    """Format orchestrator directions into a prompt-friendly summary."""
    if not dirs:
        return "No strategy orchestrator analysis available."
    lines = ["Strategy Orchestrator Analysis:"]
    for symbol, info in sorted(dirs.items()):
        if isinstance(info, dict):
            direction = info.get("direction", "neutral")
            confidence = info.get("confidence", 0)
            reason = info.get("reason", "")
            lines.append(
                f"  {symbol}: {direction.upper()} (confidence={confidence:.0%}) - {reason}"
            )
        else:
            lines.append(f"  {symbol}: {info}")
    return "\n".join(lines)


def _format_trigger_context(trigger: Dict) -> str:
    """Build a human-readable explanation of why the AI was activated."""
    trigger_type = trigger.get("trigger_type", "scheduled")
    trigger_source = trigger.get("trigger_source", "unknown")

    if trigger_type == "autonomous":
        return (
            "**自主交易触发**: AI 策略自主交易循环，用于重新评估市场。\n"
            f"  → 来源: {trigger_source}\n"
            "  → 对所有监控的交易对进行全面扫描。"
        )
    elif trigger_type == "signal":
        return (
            "**信号触发**: 预设条件已满足（如 OI 激增、资金费率飙升、价格突破）。\n"
            f"  → 来源: {trigger_source}\n"
            "  → 在行动前，重点验证触发信号的市场背景。"
        )
    else:
        return (
            "**定时触发**: 例行检查点，用于重新评估市场。\n"
            "  → 对所有监控的交易对进行全面扫描。"
        )
