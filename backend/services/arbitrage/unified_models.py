"""
统一套利数据模型

整合原有 models.py、hedge_risk_gate.py、risk_assessor.py、cross_exchange_risk.py
中重复定义的数据结构，建立单一数据源。

主要模型:
- ArbHedgePosition: 统一对冲仓位（替代 models.py 和 hedge_risk_gate.py 中的重复定义）
- ArbAccountSnapshot: 统一账户快照
- ArbRiskCheckResult: 统一风控检查结果
- ArbitrageCapitalPool: 套利资金池
- ExchangeFeeSchedule: 交易所费率配置
- ArbitragePositionMetrics: 实时仓位指标
- CompositeRiskScore: 复合风险评分
- ExecutionMode: 执行模式枚举
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Dict, List, Optional


class ArbitrageStatus(Enum):
    """套利状态枚举"""
    SCANNING = "scanning"
    OPPORTUNITY_FOUND = "opportunity_found"
    ACTIVE = "active"
    EXPIRING = "expiring"
    EXPIRED = "expired"
    ERROR = "error"


class ExecutionMode(Enum):
    """执行模式"""
    PAPER = "paper"
    LIVE = "live"


class StrategyType(Enum):
    """套利策略类型"""
    FUNDING_RATE = "funding_rate"
    CROSS_EXCHANGE_SPREAD = "cross_exchange_spread"
    SPOT_PERP_BASIS = "spot_perp_basis"


class RiskLevel(Enum):
    """风险等级"""
    SAFE = "safe"           # 0.0-0.3
    CAUTION = "caution"     # 0.3-0.5
    DANGER = "danger"       # 0.5-0.7
    CRITICAL = "critical"   # 0.7-1.0


@dataclass
class FundingRateSnapshot:
    """资金费率快照"""
    symbol: str
    current_rate: float
    predicted_rate: float
    rate_8h_avg: float
    rate_24h_avg: float
    annual_yield: float
    oi_total: float = 0.0
    volume_24h: float = 0.0
    timestamp: float = 0.0

    @property
    def is_extreme(self) -> bool:
        return abs(self.current_rate) > 0.01


@dataclass
class ArbHedgePosition:
    """
    统一对冲仓位

    整合 models.py HedgePosition + hedge_risk_gate.py HedgePosition，
    增加 strategy_type、交易所字段、清算价格等。
    """
    position_id: str
    symbol: str
    strategy: StrategyType
    long_size: float
    long_entry_price: float
    short_size: float
    short_entry_price: float
    delta: float
    accumulated_funding: float = 0.0
    entry_time: float = 0.0
    status: ArbitrageStatus = ArbitrageStatus.ACTIVE
    funding_payments_count: int = 0
    # 跨交易所字段
    exchange_long: str = ""
    exchange_short: str = ""
    # 价差套利字段
    entry_z_score: float = 0.0
    entry_spread_pct: float = 0.0
    # 基差套利字段
    entry_basis_pct: float = 0.0
    # 风险字段
    liquidation_price_long: float = 0.0
    liquidation_price_short: float = 0.0
    maintenance_margin: float = 0.0

    @property
    def is_balanced(self) -> bool:
        max_side = max(self.long_size, self.short_size, 1e-10)
        return abs(self.delta) / max_side < 0.02

    @property
    def notional(self) -> float:
        return max(
            self.long_size * self.long_entry_price,
            self.short_size * self.short_entry_price,
            0.0,
        )


@dataclass
class ArbitrageOpportunity:
    """套利机会"""
    opportunity_id: str
    symbol: str
    strategy: str
    expected_annual_yield: float
    funding_snapshot: Optional[FundingRateSnapshot] = None
    recommended_size: float = 0.0
    risk_score: float = 0.0
    confidence: float = 0.0
    timestamp: float = 0.0
    status: ArbitrageStatus = ArbitrageStatus.ACTIVE
    # 跨交易所字段
    exchange_a: str = ""
    exchange_b: str = ""
    spread_pct: float = 0.0
    z_score: float = 0.0


@dataclass
class ArbAccountSnapshot:
    """
    统一账户快照

    替代 hedge_risk_gate.py 中的 AccountSnapshot。
    """
    total_equity: float
    available_balance: float
    frozen_margin: float = 0.0
    arbitrage_pool_balance: float = 0.0
    daily_arb_pnl: float = 0.0
    realized_pnl_today: float = 0.0


@dataclass
class ArbRiskCheckResult:
    """
    统一风控检查结果

    替代 models.py HedgeRiskCheckResult 和 hedge_risk_gate.py RiskCheckResult。
    """
    passed: bool
    check_name: str = ""
    reason_code: str = ""
    reason_text: str = ""
    blocked_by: str = ""
    risk_score: float = 0.0
    details: Optional[Dict[str, Any]] = None


@dataclass
class ArbitrageCapitalPool:
    """套利资金池"""
    total_pool_usd: float = 0.0
    allocated_usd: float = 0.0
    available_usd: float = 0.0
    max_pool_pct_of_equity: float = 0.30
    daily_loss_limit_pct: float = 0.03
    daily_realized_loss: float = 0.0
    cooldown_until: float = 0.0

    @property
    def utilization_pct(self) -> float:
        if self.total_pool_usd <= 0:
            return 0.0
        return self.allocated_usd / self.total_pool_usd

    def can_allocate(self, amount: float) -> bool:
        import time
        if time.time() < self.cooldown_until:
            return False
        if self.available_usd <= 0:
            return False
        return amount <= self.available_usd


@dataclass
class ExchangeFeeSchedule:
    """交易所费率配置"""
    exchange_id: str
    maker_rate: float
    taker_rate: float
    withdrawal_fee_usd: float = 0.0
    slippage_bps_estimate: float = 5.0

    def entry_cost(self, notional: float) -> float:
        return notional * self.taker_rate

    def exit_cost(self, notional: float) -> float:
        return notional * self.taker_rate

    def round_trip_cost(self, notional: float) -> float:
        return (self.entry_cost(notional) + self.exit_cost(notional)
                + notional * self.slippage_bps_estimate / 10000)


@dataclass
class ArbitragePositionMetrics:
    """实时仓位指标"""
    position_id: str
    current_delta: float = 0.0
    delta_pct: float = 0.0
    unrealized_pnl: float = 0.0
    accumulated_funding: float = 0.0
    funding_trend: str = "stable"   # improving / stable / deteriorating
    z_score_current: float = 0.0
    liquidation_distance_pct: float = 100.0
    age_hours: float = 0.0
    edge_decay_pct: float = 0.0
    entry_edge: float = 0.0
    current_edge: float = 0.0

    @property
    def total_pnl(self) -> float:
        return self.unrealized_pnl + self.accumulated_funding


@dataclass
class CompositeRiskScore:
    """复合风险评分"""
    total_score: float = 0.0
    capital_factor: float = 0.0
    delta_factor: float = 0.0
    funding_factor: float = 0.0
    spread_factor: float = 0.0
    fee_factor: float = 0.0
    margin_factor: float = 0.0
    liquidity_factor: float = 0.0

    @property
    def level(self) -> RiskLevel:
        if self.total_score < 0.3:
            return RiskLevel.SAFE
        elif self.total_score < 0.5:
            return RiskLevel.CAUTION
        elif self.total_score < 0.7:
            return RiskLevel.DANGER
        else:
            return RiskLevel.CRITICAL

    @property
    def size_multiplier(self) -> float:
        """仓位缩放因子"""
        level = self.level
        if level == RiskLevel.SAFE:
            return 1.0
        elif level == RiskLevel.CAUTION:
            return 0.5
        elif level == RiskLevel.DANGER:
            return 0.25
        else:
            return 0.0
