"""
Symbol registry for multi-exchange market data.

The registry keeps one canonical symbol, such as BTC, while allowing each
exchange to use its own API symbol format.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Dict, Optional


@dataclass(frozen=True)
class SymbolMapping:
    exchange: str
    canonical_symbol: str
    exchange_symbol: str
    market_type: str = "perp"
    quote_asset: str = "USDC"
    active: bool = True


class SymbolRegistry:
    """In-memory symbol registry with deterministic fallback mapping."""

    def __init__(self) -> None:
        self._mappings: Dict[tuple[str, str, str], SymbolMapping] = {}
        self._seed_defaults()

    def _seed_defaults(self) -> None:
        for exchange in ("hyperliquid", "asterdex", "binance", "bybit", "okx", "gateio"):
            for symbol in ("BTC", "ETH", "SOL", "BNB", "ASTER"):
                self.register(exchange=exchange, canonical_symbol=symbol)

    @staticmethod
    def normalize_exchange(exchange: str) -> str:
        exchange_key = (exchange or "").strip().lower()
        if exchange_key == "aster":
            return "asterdex"
        return exchange_key

    @staticmethod
    def normalize_canonical(symbol: str) -> str:
        symbol = (symbol or "").strip().upper()
        for suffix in ("USDT", "USDC", "USD"):
            if symbol.endswith(suffix) and len(symbol) > len(suffix):
                return symbol[:-len(suffix)]
        return symbol.replace("/", "").split(":")[0]

    def register(
        self,
        exchange: str,
        canonical_symbol: str,
        exchange_symbol: Optional[str] = None,
        market_type: str = "perp",
        quote_asset: Optional[str] = None,
        active: bool = True,
    ) -> SymbolMapping:
        exchange_key = self.normalize_exchange(exchange)
        canonical = self.normalize_canonical(canonical_symbol)
        quote = (quote_asset or self.default_quote_asset(exchange_key)).upper()
        mapping = SymbolMapping(
            exchange=exchange_key,
            canonical_symbol=canonical,
            exchange_symbol=exchange_symbol or self.default_exchange_symbol(exchange_key, canonical, quote),
            market_type=market_type,
            quote_asset=quote,
            active=active,
        )
        self._mappings[(exchange_key, market_type, canonical)] = mapping
        return mapping

    def resolve(self, exchange: str, symbol: str, market_type: str = "perp") -> SymbolMapping:
        exchange_key = self.normalize_exchange(exchange)
        canonical = self.normalize_canonical(symbol)
        mapping = self._mappings.get((exchange_key, market_type, canonical))
        if mapping:
            return mapping
        return self.register(exchange=exchange_key, canonical_symbol=canonical, market_type=market_type)

    def list_exchange(self, exchange: str, market_type: str = "perp") -> list[SymbolMapping]:
        exchange_key = self.normalize_exchange(exchange)
        return [
            mapping for (ex, mt, _), mapping in sorted(self._mappings.items())
            if ex == exchange_key and mt == market_type and mapping.active
        ]

    @staticmethod
    def default_quote_asset(exchange: str) -> str:
        exchange = SymbolRegistry.normalize_exchange(exchange)
        if exchange == "hyperliquid":
            return "USDC"
        return "USDT"

    @staticmethod
    def default_exchange_symbol(exchange: str, canonical: str, quote: str) -> str:
        exchange = SymbolRegistry.normalize_exchange(exchange)
        if exchange == "hyperliquid":
            return f"{canonical}/USDC:USDC"
        return f"{canonical}/{quote}:{quote}"


symbol_registry = SymbolRegistry()
