"""
Shared type definitions for the prompt_context package.

All builders accept BuildInput and return Dict[str, Any] compatible with
existing template.format_map(SafeDict(...)) call sites.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from sqlalchemy.orm import Session
from backend.database.models import Account


@dataclass
class BuildInput:
    """Unified input bag for all context builders.
    
    Mirrors the parameter list of the original _build_prompt_context().
    Not all fields are required by every builder — each builder documents
    which fields it consumes.
    """
    # ── Core ──
    account: Account
    db: Optional[Session] = None
    now_utc: datetime = field(default_factory=lambda: datetime.now(timezone.utc))

    # ── Portfolio & Prices ──
    portfolio: Dict[str, Any] = field(default_factory=dict)
    prices: Dict[str, float] = field(default_factory=dict)

    # ── Exchange State ──
    hyperliquid_state: Optional[Dict[str, Any]] = None
    binance_state: Optional[Dict[str, Any]] = None
    environment: str = "mainnet"  # mainnet | testnet

    # ── Symbols ──
    symbol_metadata: Optional[Dict[str, Any]] = None
    symbol_order: Optional[List[str]] = None
    target_symbol: Optional[str] = None  # legacy
    samples: Optional[List] = None  # legacy
    sampling_interval: Optional[int] = None

    # ── Template & Trigger ──
    template_text: Optional[str] = None
    trigger_context: Optional[Dict[str, Any]] = None

    # ── Computed helpers (set by coordinator) ──
    ordered_symbols: List[str] = field(default_factory=list)
    normalized_symbol_metadata: Dict[str, Any] = field(default_factory=dict)
    symbol_display_map: Dict[str, str] = field(default_factory=dict)
    max_leverage: int = 10
    default_leverage: int = 10


# BuildResult is just Dict[str, Any] for maximum compatibility
BuildResult = Dict[str, Any]
