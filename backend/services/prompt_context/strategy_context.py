"""
Strategy context builder — factor engine, RAG analogies, trading wisdom,
confidence calibration, adaptive trading summary.
"""
from __future__ import annotations

from typing import Any, Dict

from .types import BuildInput, BuildResult


def _build_strategy_context(inp: BuildInput) -> BuildResult:
    """Build strategy-level context: factors, RAG, wisdom, calibration.

    Consumes: inp.account, inp.ordered_symbols, inp.trigger_context,
              inp.db, inp.hyperliquid_state.
    """
    result: Dict[str, Any] = {}

    # Factor engine status — computed by ai_decision_integration
    result["factor_engine_status"] = _build_factor_engine(inp)
    result["adaptive_trading_summary"] = _build_adaptive_trading(inp)
    result["factors_summary"] = "N/A"  # filled by call_ai_for_decision

    # Historical analogies (RAG)
    result["historical_analogies"] = "N/A"  # filled by call_ai_for_decision

    # K-line technical analysis
    result["kline_technical_analysis"] = "N/A"  # filled by call_ai_for_decision

    # Confidence calibration
    result["confidence_calibration"] = "N/A"  # filled by call_ai_for_decision

    # Trading wisdom from backtest evolution
    result["strategy_wisdom"] = _build_strategy_wisdom(inp)

    # Trader personality and mental state
    result["trader_personality"] = _build_trader_personality(inp)
    result["trader_mental_state"] = "Normal — no anomalies detected."

    return result


def _build_factor_engine(inp: BuildInput) -> str:
    """Build factor engine status string via ai_decision_integration."""
    try:
        from backend.services.ai_decision_integration import build_factor_context
        fc = build_factor_context(inp.account.id, inp.ordered_symbols, inp.db)
        if fc and fc.top_factors:
            lines = ["Factor Engine Status:"]
            for f in fc.top_factors[:10]:
                lines.append(
                    f"  - {f.get('name', '?')}: "
                    f"value={f.get('value', 0):.3f}, "
                    f"signal={f.get('signal', 'neutral')}, "
                    f"weight={f.get('weight', 0):.2f}"
                )
            regime = fc.market_regime or "unknown"
            lines.append(f"  Market Regime: {regime}")
            return "\n".join(lines)
    except Exception:
        pass
    return "Factor engine offline — using pure AI judgment."


def _build_adaptive_trading(inp: BuildInput) -> str:
    """Build adaptive trading summary via ai_decision_integration."""
    try:
        from backend.services.ai_decision_integration import build_execution_context
        ec = build_execution_context(inp.account.id, inp.ordered_symbols, inp.db)
        if ec:
            lines = ["Adaptive Trading Parameters:"]
            for symbol in inp.ordered_symbols[:5]:
                sl = ec.stop_loss_pct.get(symbol, 0.05)
                tp = ec.take_profit_pct.get(symbol, 0.10)
                lev = ec.leverage.get(symbol, inp.default_leverage)
                lines.append(f"  {symbol}: SL={sl:.1%} TP={tp:.1%} Lev={lev}x")
            return "\n".join(lines)
    except Exception:
        pass
    return "Adaptive trading parameters: using system defaults."


def _build_strategy_wisdom(inp: BuildInput) -> str:
    """Build strategy wisdom from backtest evolution and trade history."""
    trigger = inp.trigger_context or {}
    strategy_id = trigger.get("ai_strategy_id")
    if not strategy_id or not inp.db:
        return "[风控约束] 遵守全局风控参数。\n[回测经验] 暂无足够历史数据。"

    try:
        from backend.database.models import StrategyTrade, TradingWisdom
        trades = inp.db.query(StrategyTrade).filter(
            StrategyTrade.strategy_id == strategy_id,
            StrategyTrade.pnl.isnot(None),
        ).order_by(StrategyTrade.closed_at.desc()).limit(20).all()

        # Fetch compiled wisdom
        wisdom = inp.db.query(TradingWisdom).filter(
            TradingWisdom.strategy_id == strategy_id
        ).order_by(TradingWisdom.created_at.desc()).first()

        lines = ["[风控约束]", "- 遵守全局风控参数和保证金限制"]
        if trades:
            pnls = [t.pnl for t in trades if t.pnl is not None]
            wins = sum(1 for p in pnls if p > 0)
            lines.append(f"- 策略近期胜率: {wins}/{len(pnls)} ({wins/len(pnls)*100:.0f}%)")

        lines.append("\n[回测经验]")
        if wisdom and wisdom.wisdom_text:
            lines.append(wisdom.wisdom_text[:500])
        else:
            lines.append("- 暂无回测进化经验，建议保守操作。")

        return "\n".join(lines)
    except Exception:
        return "[风控约束] 遵守全局风控参数。\n[回测经验] 数据获取失败。"


def _build_trader_personality(inp: BuildInput) -> str:
    """Build trader personality context string."""
    try:
        from backend.database.models import TraderPersonality
        from backend.config.personality_presets import PERSONALITY_PRESETS

        if inp.db:
            tp = inp.db.query(TraderPersonality).filter(
                TraderPersonality.account_id == inp.account.id
            ).first()
            if tp and tp.preset_id and tp.preset_id in PERSONALITY_PRESETS:
                preset = PERSONALITY_PRESETS[tp.preset_id]
                return (
                    f"Trader Personality: {preset.get('name', tp.preset_id)}\n"
                    f"Style: {preset.get('description', 'Custom')}\n"
                    f"Risk tolerance: {preset.get('risk_tolerance', 'medium')}"
                )
    except Exception:
        pass
    return "Trader Personality: Default (balanced)"
