"""
Market context builder — prices, sampling data, kline and indicator formatting.
Delegates to existing helpers in ai_decision_service.py.
"""
from __future__ import annotations

from typing import Any, Dict

from .types import BuildInput, BuildResult


def _build_market_context(inp: BuildInput) -> BuildResult:
    """Build market-level context: prices, snapshots, sampling, klines.

    Consumes: inp.prices, inp.ordered_symbols, inp.symbol_display_map,
              inp.symbol_order, inp.template_text, inp.samples,
              inp.target_symbol, inp.sampling_interval.
    """
    from backend.services.ai_decision_service import (
        _build_market_snapshot,
        _build_market_prices,
        _build_sampling_data,
    )

    result: Dict[str, Any] = {}

    positions: Dict[str, Any] = (inp.portfolio or {}).get("positions") or {}

    # Legacy market snapshot
    result["market_snapshot"] = _build_market_snapshot(
        inp.prices, positions, inp.ordered_symbols
    )

    # Formatted price table
    result["market_prices"] = _build_market_prices(
        inp.prices, inp.ordered_symbols, inp.symbol_display_map
    )

    # Legacy sampling (single symbol)
    result["sampling_data"] = _build_sampling_data(
        inp.samples, inp.target_symbol, inp.sampling_interval
    )

    return result
