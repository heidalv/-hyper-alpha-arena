"""
PromptContextBuilder — master coordinator that delegates to sub-builders.

Replaces the monolithic _build_prompt_context() in ai_decision_service.py.
The public API `build()` returns the same Dict[str, Any] that the existing
template.format_map() expects — zero change to callers.
"""
from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional

from sqlalchemy.orm import Session
from backend.database.models import Account

from .types import BuildInput, BuildResult
from .account_context import _build_account_context
from .market_context import _build_market_context
from .exchange_context import _build_exchange_context
from .intelligence_context import _build_intelligence_context
from .strategy_context import _build_strategy_context

logger = logging.getLogger(__name__)


class PromptContextBuilder:
    """Master coordinator that assembles the final prompt context dict
    by delegating to five focused sub-builders.

    Usage::

        builder = PromptContextBuilder()
        context = builder.build(BuildInput(
            account=account,
            db=db,
            portfolio=portfolio,
            prices=prices,
            ...
        ))
        prompt = template.format_map(SafeDict(context))
    """

    # ── Public API ──────────────────────────────────────────────

    def build(self, inp: BuildInput) -> BuildResult:
        """Build the complete prompt context dictionary.

        Returns a flat dict with ~60+ keys compatible with
        existing prompt templates.
        """
        # Phase 0: normalize symbols (shared across all builders)
        self._normalize_symbols(inp)

        # Phase 1: resolve leverage (needed by exchange and account builders)
        self._resolve_leverage(inp)

        # Phase 2: delegate to sub-builders (order matters — later builders
        #          may reference keys set by earlier ones)
        result: BuildResult = {}

        result.update(_build_account_context(inp))
        result.update(_build_market_context(inp))
        result.update(_build_exchange_context(inp))
        result.update(_build_intelligence_context(inp))
        result.update(_build_strategy_context(inp))

        # Phase 3: assemble legacy compatibility keys
        result.update(self._build_legacy_keys(inp, result))

        return result

    # ── Helpers ─────────────────────────────────────────────────

    def _normalize_symbols(self, inp: BuildInput) -> None:
        """Normalize symbol order and metadata into inp."""
        from backend.services.ai_decision_service import SUPPORTED_SYMBOLS

        src = inp.symbol_metadata or SUPPORTED_SYMBOLS
        base_order = inp.symbol_order or list(src.keys())

        ordered: List[str] = []
        seen: set = set()
        for sym in base_order:
            upper = str(sym).upper()
            if not upper or upper in seen:
                continue
            seen.add(upper)
            ordered.append(upper)
        if not ordered:
            ordered = list(SUPPORTED_SYMBOLS.keys())

        inp.ordered_symbols = ordered
        inp.normalized_symbol_metadata = self._normalize_meta(src, ordered)
        inp.symbol_display_map = {
            s: inp.normalized_symbol_metadata.get(s, {}).get("name") or SUPPORTED_SYMBOLS.get(s, s)
            for s in ordered
        }

    @staticmethod
    def _normalize_meta(meta: Dict[str, Any], ordered: List[str]) -> Dict[str, Any]:
        """Ensure metadata keys are uppercase."""
        return {k.upper(): v for k, v in (meta or {}).items()}

    def _resolve_leverage(self, inp: BuildInput) -> None:
        """Resolve default/max leverage from account + TraderPersonality."""
        if inp.db:
            try:
                from services.hyperliquid_environment import get_leverage_settings
                settings = get_leverage_settings(inp.db, inp.account.id, inp.environment)
                inp.max_leverage = settings["max_leverage"]
                inp.default_leverage = settings["default_leverage"]
            except Exception as e:
                logger.warning("Leverage resolution via service failed: %s — using fallback", e)
                inp.max_leverage = getattr(inp.account, "max_leverage", 20)
                inp.default_leverage = getattr(inp.account, "default_leverage", 10)
        else:
            inp.max_leverage = getattr(inp.account, "max_leverage", 20)
            inp.default_leverage = getattr(inp.account, "default_leverage", 10)

        # TraderPersonality override
        if inp.db:
            try:
                from backend.database.models import TraderPersonality
                tp = inp.db.query(TraderPersonality).filter(
                    TraderPersonality.account_id == inp.account.id
                ).first()
                if tp and tp.preferred_leverage and tp.max_leverage:
                    inp.default_leverage = tp.preferred_leverage
                    inp.max_leverage = tp.max_leverage
            except Exception:
                pass

    def _build_legacy_keys(self, inp: BuildInput, result: BuildResult) -> BuildResult:
        """Build keys for backward compatibility with old templates."""
        from backend.services.ai_decision_service import SUPPORTED_SYMBOLS, OUTPUT_FORMAT_COMPLETE, SYMBOL_PLACEHOLDER, MAX_LEVERAGE_PLACEHOLDER

        symbols_csv = ", ".join(inp.ordered_symbols) if inp.ordered_symbols else "N/A"
        symbol_choices = "|".join(inp.ordered_symbols) if inp.ordered_symbols else "SYMBOL"

        # Build formatted output
        output_format = OUTPUT_FORMAT_COMPLETE.replace(
            SYMBOL_PLACEHOLDER, symbol_choices
        ).replace(
            MAX_LEVERAGE_PLACEHOLDER, str(inp.max_leverage)
        )

        # Legacy format with just symbol replacement
        from backend.services.ai_decision_service import OUTPUT_FORMAT_JSON
        legacy_output = OUTPUT_FORMAT_JSON.replace(SYMBOL_PLACEHOLDER, symbol_choices)

        return {
            "selected_symbols_detail": result.get("selected_symbols_detail", ""),
            "selected_symbols_csv": symbols_csv,
            "output_symbol_choices": symbol_choices,
            "output_format": output_format,
            "output_format_legacy": legacy_output,
            "default_leverage": inp.default_leverage,
            "max_leverage": inp.max_leverage,
        }
