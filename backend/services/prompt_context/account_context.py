"""
Account context builder — portfolio, holdings, margin, leverage, session info.
Delegates to existing helpers in ai_decision_service.py.
"""
from __future__ import annotations

from typing import Any, Dict

from .types import BuildInput, BuildResult


def _build_account_context(inp: BuildInput) -> BuildResult:
    """Build account-level context for prompt rendering.

    Consumes: inp.account, inp.portfolio, inp.prices, inp.hyperliquid_state,
              inp.binance_state, inp.environment, inp.max_leverage,
              inp.default_leverage, inp.ordered_symbols, inp.symbol_display_map.
    """
    from backend.services.ai_decision_service import (
        _build_account_state,
        _build_session_context,
        _calculate_runtime_minutes,
        _calculate_total_return_percent,
        _format_currency,
        _build_holdings_detail,
    )

    result: Dict[str, Any] = {}

    portfolio = inp.portfolio or {}
    now = inp.now_utc

    # Legacy format
    result["account_state"] = _build_account_state(portfolio)
    result["session_context"] = _build_session_context(inp.account)

    # Numeric
    result["runtime_minutes"] = _calculate_runtime_minutes(inp.account)
    result["current_time_utc"] = now.isoformat() + "Z"
    result["total_return_percent"] = _calculate_total_return_percent(inp.account)

    # Cash / margin
    result["available_cash"] = _format_currency(portfolio.get("cash"))
    result["total_account_value"] = _format_currency(portfolio.get("total_assets"))
    result["margin_info"] = ""

    # Holdings
    positions = portfolio.get("positions") or {}
    result["holdings_detail"] = _build_holdings_detail(positions)

    # Leverage (pre-resolved by builder coordinator)
    result["default_leverage"] = inp.default_leverage
    result["max_leverage"] = inp.max_leverage

    return result
