"""
Exchange context builder — Hyperliquid / Binance / Paper-specific formatting.
Handles positions detail, margin warnings, leverage constraints, real-trading alerts.
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Dict

from .types import BuildInput, BuildResult


def _build_exchange_context(inp: BuildInput) -> BuildResult:
    """Build exchange-specific context: Hyperliquid, Binance, or Paper.

    Consumes: inp.hyperliquid_state, inp.binance_state, inp.environment,
              inp.max_leverage, inp.default_leverage, inp.prices,
              inp.ordered_symbols, inp.db.
    """
    result: Dict[str, Any] = {}

    hl = inp.hyperliquid_state
    bn = inp.binance_state
    env = inp.environment

    if hl and env in ("testnet", "mainnet"):
        result.update(_build_hyperliquid_context(inp, hl))
    elif bn:
        result.update(_build_binance_context(inp, bn))
    else:
        result.update(_build_paper_context(inp))

    return result


# ── Hyperliquid ─────────────────────────────────────────────────

def _build_hyperliquid_context(inp: BuildInput, hl: Dict[str, Any]) -> Dict[str, Any]:
    from backend.services.ai_decision_service import _format_currency

    env = inp.environment
    result: Dict[str, Any] = {}

    # Margin mode
    is_isolated = True
    if inp.db:
        try:
            from backend.database.models import SystemConfig
            cfg = inp.db.query(SystemConfig).filter(SystemConfig.key == "global_margin_mode").first()
            if cfg and cfg.value == "cross":
                is_isolated = False
        except Exception:
            pass

    margin_label = "ISOLATED" if is_isolated else "CROSS"
    margin_desc = (
        "each position has independent margin"
        if is_isolated
        else "all positions share account margin"
    )
    margin_risk = (
        "Each position is isolated — one liquidation does NOT affect other positions"
        if is_isolated
        else "Cross margin — all positions share balance, higher capital efficiency but liquidation risk is shared"
    )

    result["trading_environment"] = f"Platform: Hyperliquid Perpetual Contracts | Environment: {env.upper()}"

    if env == "mainnet":
        result["real_trading_warning"] = "⚠️ REAL MONEY TRADING - All decisions execute on live markets"
        result["operational_constraints"] = (
            f"- Perpetual contract trading with {margin_label} margin ({margin_desc})\n"
            f"- Maximum position size: ≤ 25% of available balance per trade\n"
            f"- Leverage range: {inp.default_leverage}x to {inp.max_leverage}x (default: {inp.default_leverage}x)\n"
            f"- Margin call threshold: 80% margin usage (CRITICAL - will auto-liquidate)\n"
            f"- Default stop loss: -10% from entry (adjust based on leverage and volatility)\n"
            f"- Default take profit: +20% from entry (adjust based on risk/reward)\n"
            f"- Liquidation protection: NEVER exceed 70% margin usage\n"
            f"- Risk management: {margin_risk}"
        )
    else:
        result["real_trading_warning"] = "Testnet simulation environment (using test funds)"
        result["operational_constraints"] = (
            f"- Perpetual contract trading with {margin_label} margin ({margin_desc}, testnet mode)\n"
            f"- Default position size: ≤ 30% of available balance per trade\n"
            f"- Leverage range: {inp.default_leverage}x to {inp.max_leverage}x (default: {inp.default_leverage}x)\n"
            f"- Margin call threshold: 80% margin usage\n"
            f"- Default stop loss: -8% from entry (adjust based on leverage)\n"
            f"- Default take profit: +15% from entry\n"
            f"- Liquidation protection: avoid exceeding 70% margin usage\n"
            f"- {margin_risk}"
        )

    result["leverage_constraints"] = (
        f"- Leverage range: {inp.default_leverage}x to {inp.max_leverage}x (default: {inp.default_leverage}x)"
    )
    result["margin_info"] = f"\nMargin Mode: {margin_label} margin ({margin_desc})"

    # Positions detail
    result["total_equity"] = _format_currency(hl.get("total_equity"))
    result["available_balance"] = _format_currency(hl.get("available_balance"))
    result["used_margin"] = _format_currency(hl.get("used_margin", 0))
    result["margin_usage_percent"] = f"{hl.get('margin_usage_percent', 0):.1f}"
    result["maintenance_margin"] = _format_currency(hl.get("maintenance_margin", 0))
    result["positions_detail"] = _format_hl_positions(hl.get("positions", []), inp.prices)

    return result


def _format_hl_positions(hl_positions: list, prices: Dict[str, float]) -> str:
    """Format Hyperliquid positions into a readable string block."""
    from backend.services.ai_decision_service import _format_currency

    if not hl_positions:
        return "No open positions"

    lines = []
    for pos in hl_positions:
        symbol = (pos.get("coin") or "UNKNOWN").upper()
        size = float(pos.get("szi", 0) or 0)
        direction = "Long" if size > 0 else "Short"
        abs_size = abs(size)
        entry_px = float(pos.get("entry_px", 0) or 0)
        unrealized_pnl = float(pos.get("unrealized_pnl", 0) or 0)
        leverage = float(pos.get("leverage", 1))
        margin_used = float(pos.get("margin_used", 0) or 0)
        position_value = float(pos.get("position_value", 0) or 0)
        roe = float(pos.get("return_on_equity", 0) or 0)
        funding_total = float(pos.get("cum_funding_all_time", 0) or 0)
        liquidation_px = float(pos.get("liquidation_px", 0) or 0)
        leverage_type = pos.get("leverage_type", "cross") or "cross"
        current_price = prices.get(symbol, entry_px)

        pnl_str = f"+${unrealized_pnl:,.2f}" if unrealized_pnl >= 0 else f"-${abs(unrealized_pnl):,.2f}"
        roe_str = f"+{roe:.2f}%" if roe >= 0 else f"{roe:.2f}%"
        funding_str = f"+${funding_total:.4f}" if funding_total >= 0 else f"-${abs(funding_total):.4f}"

        liq_dist_pct = abs(current_price - liquidation_px) / current_price * 100 if liquidation_px > 0 and current_price > 0 else 0
        liq_warn = " ⚠️" if liq_dist_pct < 10 else ""

        lines.append(
            f"- {symbol}: {direction} {abs_size:.4f} units @ ${entry_px:,.2f} avg\n"
            f"  Mark price: ${current_price:,.2f} | Position value: ${position_value:,.2f}\n"
            f"  Unrealized P&L: {pnl_str} ({roe_str} ROE)\n"
            f"  Leverage: {leverage:.0f}x {leverage_type.capitalize()} | Margin: ${margin_used:,.2f}\n"
            f"  Liquidation: ${liquidation_px:,.2f} ({liq_dist_pct:.1f}% away){liq_warn} | Funding: {funding_str}"
        )
    return "\n".join(lines)


# ── Binance ─────────────────────────────────────────────────────

def _build_binance_context(inp: BuildInput, bn: Dict[str, Any]) -> Dict[str, Any]:
    from backend.services.ai_decision_service import _format_currency

    result: Dict[str, Any] = {}
    result["trading_environment"] = "Platform: Binance Futures"
    result["real_trading_warning"] = ""
    result["operational_constraints"] = ""
    result["leverage_constraints"] = ""
    result["margin_info"] = ""
    result["total_equity"] = _format_currency(bn.get("total_balance"))
    result["available_balance"] = _format_currency(bn.get("available_balance"))
    result["used_margin"] = _format_currency(bn.get("margin_used", 0))
    total_bal = float(bn.get("total_balance", 0) or 0)
    margin_u = float(bn.get("margin_used", 0) or 0)
    result["margin_usage_percent"] = f"{(margin_u / total_bal) * 100:.1f}" if total_bal > 0 else "0"
    result["maintenance_margin"] = _format_currency(bn.get("maintenance_margin", 0))
    result["positions_detail"] = "No open positions"
    return result


# ── Paper ───────────────────────────────────────────────────────

def _build_paper_context(inp: BuildInput) -> Dict[str, Any]:
    result: Dict[str, Any] = {}
    result["trading_environment"] = "Platform: Paper Trading Simulation"
    result["real_trading_warning"] = "Sandbox environment (no real funds at risk)"
    result["operational_constraints"] = (
        "- No pyramiding or position size increases without explicit exit plan\n"
        "- Default risk per trade: ≤ 20% of available cash\n"
        "- Default stop loss: -5% from entry (adjust based on volatility)\n"
        "- Default take profit: +10% from entry (adjust based on signals)"
    )
    result["leverage_constraints"] = ""
    result["margin_info"] = ""
    result["total_equity"] = "N/A"
    result["available_balance"] = "N/A"
    result["used_margin"] = "N/A"
    result["margin_usage_percent"] = "0"
    result["maintenance_margin"] = "N/A"
    result["positions_detail"] = "No open positions"
    return result
