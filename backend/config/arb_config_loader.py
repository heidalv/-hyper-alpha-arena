"""
V3 套利系统配置加载器

Usage:
    from backend.config.arb_config_loader import arb_config
"""

from __future__ import annotations

import logging
import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional

import yaml

logger = logging.getLogger(__name__)

_CONFIG_DIR = Path(__file__).parent
_DEFAULT_YAML = _CONFIG_DIR / "arb_config.yaml"


@dataclass
class ArbEngineConfig:
    default_mode: str = "paper"
    max_pool_pct_of_equity: float = 0.30
    daily_loss_limit_pct: float = 0.03
    position_size_pct: float = 0.20


@dataclass
class ArbScannerConfig:
    min_annual_yield: float = 0.15
    min_history_periods: int = 24
    basis_scan_enabled: bool = False
    basis_entry_threshold_pct: float = 0.003
    cross_exchange_entry_zscore: float = 2.0
    cross_exchange_exit_zscore: float = 0.5
    cross_exchange_min_history: int = 5
    mid_cache_ttl_sec: float = 5.0
    exchange_priority: List[str] = field(default_factory=lambda: [
        "hyperliquid", "binance", "bybit", "okx", "asterdex", "gateio",
    ])


@dataclass
class ArbFundingConfig:
    primary_exchange: str = "asterdex"
    hedge_exchange: str = "binance"
    min_annual_yield: float = 0.15
    max_position_usd: float = 10000.0
    max_holding_hours: int = 72


@dataclass
class ArbCapitalAllocationConfig:
    v3_arbitrage_total: float = 0.30
    funding_rate_arb: float = 0.10
    cross_exchange_spread: float = 0.20
    rebate_points_arb: float = 0.60
    emergency_reserve: float = 0.10


@dataclass
class ArbWsFeedConfig:
    enabled: bool = True
    poll_interval_sec: float = 2.0
    symbols: List[str] = field(default_factory=lambda: ["BTC", "ETH", "SOL"])


@dataclass
class ArbMarketDataHubConfig:
    enabled: bool = True
    stale_ttl_sec: float = 5.0
    rest_fallback_interval_sec: float = 30.0
    disable_rest_market_stream: bool = True
    primary_exchange: str = "asterdex"
    symbols: List[str] = field(default_factory=lambda: ["BTC", "ETH", "SOL"])
    channels: List[str] = field(
        default_factory=lambda: ["l2_book", "trades", "funding", "asset_ctx"]
    )


@dataclass
class ArbConfig:
    engine: ArbEngineConfig = field(default_factory=ArbEngineConfig)
    scanner: ArbScannerConfig = field(default_factory=ArbScannerConfig)
    funding: ArbFundingConfig = field(default_factory=ArbFundingConfig)
    capital_allocation: ArbCapitalAllocationConfig = field(
        default_factory=ArbCapitalAllocationConfig
    )
    ws_feed: ArbWsFeedConfig = field(default_factory=ArbWsFeedConfig)
    market_data_hub: ArbMarketDataHubConfig = field(
        default_factory=ArbMarketDataHubConfig
    )


def _apply_env_overrides(raw: Dict[str, Any]) -> Dict[str, Any]:
    prefix = "ARB_"
    for key, val in os.environ.items():
        if not key.startswith(prefix):
            continue
        parts = key[len(prefix):].lower().split("_", 1)
        if len(parts) != 2:
            continue
        section, param = parts
        if section not in raw or not isinstance(raw[section], dict):
            continue
        if val.lower() in ("true", "false"):
            raw[section][param] = val.lower() == "true"
        else:
            try:
                num = float(val)
                raw[section][param] = int(num) if num == int(num) else num
            except ValueError:
                raw[section][param] = val
    return raw


def _build_dataclass(cls, data: Optional[Dict]) -> Any:
    if not data:
        return cls()
    valid = {f.name for f in cls.__dataclass_fields__.values()}
    return cls(**{k: v for k, v in data.items() if k in valid})


def load_config(yaml_path: Optional[Path] = None) -> ArbConfig:
    path = yaml_path or _DEFAULT_YAML
    raw: Dict[str, Any] = {}
    if path.exists():
        try:
            with open(path, "r", encoding="utf-8") as f:
                raw = yaml.safe_load(f) or {}
            logger.info("[ArbConfig] Loaded from %s", path)
        except Exception as e:
            logger.warning("[ArbConfig] Failed to load %s: %s", path, e)
    raw = _apply_env_overrides(raw)
    return ArbConfig(
        engine=_build_dataclass(ArbEngineConfig, raw.get("engine")),
        scanner=_build_dataclass(ArbScannerConfig, raw.get("scanner")),
        funding=_build_dataclass(ArbFundingConfig, raw.get("funding")),
        capital_allocation=_build_dataclass(
            ArbCapitalAllocationConfig, raw.get("capital_allocation")
        ),
        ws_feed=_build_dataclass(ArbWsFeedConfig, raw.get("ws_feed")),
        market_data_hub=_build_dataclass(
            ArbMarketDataHubConfig, raw.get("market_data_hub")
        ),
    )


arb_config: ArbConfig = load_config()


def reload_config(yaml_path: Optional[Path] = None) -> ArbConfig:
    global arb_config
    arb_config = load_config(yaml_path)
    return arb_config
