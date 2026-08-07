"""
Rebate Arbitrage Configuration Loader

Loads config from YAML file with environment variable overrides.
Usage:
    from backend.config.rebate_config_loader import rebate_config
    print(rebate_config.engine.paper_mode)
"""

import os
import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, Optional

import yaml

logger = logging.getLogger(__name__)

_CONFIG_DIR = Path(__file__).parent
_DEFAULT_YAML = _CONFIG_DIR / "rebate_arb_config.yaml"


@dataclass
class EngineConfig:
    paper_mode: bool = True
    auto_execute: bool = False
    min_monthly_value: float = 50.0
    max_position_usd: float = 5000.0
    max_holding_days: int = 30
    max_total_volume_7d: float = 50000.0
    tick_interval_seconds: int = 90

    @property
    def default_paper_mode(self) -> bool:
        """Backward-compatible alias used by older engine code."""
        return self.paper_mode


@dataclass
class RiskGateConfig:
    max_daily_volume_per_exchange: float = 10000.0
    max_weekly_volume_per_exchange: float = 50000.0
    min_active_days_per_week: int = 2
    wash_trade_threshold: float = 0.7
    single_exchange_exposure_pct: float = 0.25
    total_rebate_exposure_pct: float = 0.30
    min_value_ratio: float = 0.005
    campaign_deadline_critical_days: int = 3
    daily_loss_circuit_breaker_pct: float = 0.03
    fee_change_alert_pct: float = 0.50

    # Backward-compatible aliases used by risk_gate.py / API patches.
    @property
    def max_wash_trade_score(self) -> float:
        return self.wash_trade_threshold

    @property
    def max_single_exchange_exposure_pct(self) -> float:
        return self.single_exchange_exposure_pct

    @property
    def max_total_rebate_exposure_pct(self) -> float:
        return self.total_rebate_exposure_pct

    @property
    def min_volume_value_ratio(self) -> float:
        return self.min_value_ratio

    @property
    def campaign_critical_days(self) -> int:
        return self.campaign_deadline_critical_days

    @property
    def max_daily_loss_pct(self) -> float:
        return self.daily_loss_circuit_breaker_pct

    @property
    def max_fee_change_pct(self) -> float:
        return self.fee_change_alert_pct


@dataclass
class CapitalAllocationConfig:
    funding_rate_arb: float = 0.40
    cross_exchange_spread: float = 0.25
    rebate_points_arb: float = 0.25
    emergency_reserve: float = 0.10
    strategy_sub_pools: Dict[str, float] = field(default_factory=lambda: {
        "S1": 0.08,
        "S2": 0.05,
        "S3": 0.20,
        "S4": 0.05,
        "S5": 0.12,
        "S6": 0.10,
        "S8": 0.30,
        "S7": 0.0,
    })


@dataclass
class WashTradeConfig:
    max_daily_volume_equity_mult: float = 2.0
    poisson_lambda: float = 0.02
    size_randomization_pct: float = 0.15


@dataclass
class CacheTTLConfig:
    fee_tier_seconds: int = 3600
    points_seconds: int = 300
    rebate_info_seconds: int = 600
    campaigns_seconds: int = 1800


@dataclass
class ExchangeItemConfig:
    enabled: bool = True
    default_maker_rate: float = 0.0002
    default_taker_rate: float = 0.0005
    default_rebate_rate: float = 0.0
    use_bnb_discount: bool = False


@dataclass
class StrategyItemConfig:
    enabled: bool = True
    params: Dict[str, Any] = field(default_factory=dict)


@dataclass
class RebateArbConfig:
    engine: EngineConfig = field(default_factory=EngineConfig)
    risk_gate: RiskGateConfig = field(default_factory=RiskGateConfig)
    capital_allocation: CapitalAllocationConfig = field(default_factory=CapitalAllocationConfig)
    wash_trade: WashTradeConfig = field(default_factory=WashTradeConfig)
    cache_ttls: CacheTTLConfig = field(default_factory=CacheTTLConfig)
    exchanges: Dict[str, ExchangeItemConfig] = field(default_factory=dict)
    strategies: Dict[str, StrategyItemConfig] = field(default_factory=dict)

    def get_strategy_config(self, strategy_id: str) -> StrategyItemConfig:
        """Return config for S1-S8 regardless of YAML key suffix."""
        sid = (strategy_id or "").upper()
        for key, item in self.strategies.items():
            if key.upper() == sid or key.upper().startswith(f"{sid}_"):
                return item
        return StrategyItemConfig()


def _apply_env_overrides(raw: Dict[str, Any]) -> Dict[str, Any]:
    """Apply environment variable overrides with REBATE_ARB_ prefix."""
    prefix = "REBATE_ARB_"
    for key, val in os.environ.items():
        if not key.startswith(prefix):
            continue
        parts = key[len(prefix):].lower().split("_", 1)
        if len(parts) == 2:
            section, param = parts
            if section in raw and isinstance(raw[section], dict):
                # Auto-cast
                if val.lower() in ("true", "false"):
                    raw[section][param] = val.lower() == "true"
                else:
                    try:
                        raw[section][param] = float(val)
                        if raw[section][param] == int(raw[section][param]):
                            raw[section][param] = int(raw[section][param])
                    except ValueError:
                        raw[section][param] = val
    return raw


def _build_dataclass(cls, data: Optional[Dict]) -> Any:
    """Build a dataclass from a dict, ignoring extra keys."""
    if not data:
        return cls()
    valid_fields = {f.name for f in cls.__dataclass_fields__.values()}
    filtered = {k: v for k, v in data.items() if k in valid_fields}
    return cls(**filtered)


def load_config(yaml_path: Optional[Path] = None) -> RebateArbConfig:
    """Load rebate arb config from YAML + env overrides."""
    path = yaml_path or _DEFAULT_YAML

    raw: Dict[str, Any] = {}
    if path.exists():
        try:
            with open(path, "r", encoding="utf-8") as f:
                raw = yaml.safe_load(f) or {}
            logger.info(f"[RebateConfig] Loaded from {path}")
        except Exception as e:
            logger.warning(f"[RebateConfig] Failed to load {path}: {e}, using defaults")
    else:
        logger.warning(f"[RebateConfig] File not found: {path}, using defaults")

    raw = _apply_env_overrides(raw)

    config = RebateArbConfig(
        engine=_build_dataclass(EngineConfig, raw.get("engine")),
        risk_gate=_build_dataclass(RiskGateConfig, raw.get("risk_gate")),
        capital_allocation=_build_dataclass(CapitalAllocationConfig, raw.get("capital_allocation")),
        wash_trade=_build_dataclass(WashTradeConfig, raw.get("wash_trade")),
        cache_ttls=_build_dataclass(CacheTTLConfig, raw.get("cache_ttls")),
    )

    # Build exchange configs
    exchanges_raw = raw.get("exchanges", {})
    for name, exc_data in exchanges_raw.items():
        if isinstance(exc_data, dict):
            config.exchanges[name] = _build_dataclass(ExchangeItemConfig, exc_data)
        else:
            config.exchanges[name] = ExchangeItemConfig()

    # Build strategy configs
    strategies_raw = raw.get("strategies", {})
    for name, strat_data in strategies_raw.items():
        if isinstance(strat_data, dict):
            enabled = strat_data.pop("enabled", True)
            config.strategies[name] = StrategyItemConfig(enabled=enabled, params=strat_data)
        else:
            config.strategies[name] = StrategyItemConfig()

    return config


# Singleton instance
rebate_config: RebateArbConfig = load_config()


def reload_config(yaml_path: Optional[Path] = None) -> RebateArbConfig:
    """Hot-reload config (returns new instance, updates singleton)."""
    global rebate_config
    rebate_config = load_config(yaml_path)
    logger.info("[RebateConfig] Config reloaded")
    return rebate_config
