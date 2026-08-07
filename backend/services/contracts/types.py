"""
Lean 5 层契约 dataclass（P2.1，方案 §1.2）。

强契约：跨 L2→L3→L4→L5 的函数签名必须以上列 dataclass 为参数/返回。
CI 契约检查器（scripts/check_contracts.py）在 types.py 建立后启用强制。

设计原则：
    - frozen=True（不可变，事件溯源友好）
    - 零业务依赖（不 import 任何 service，只 numpy/dataclass/enum）
    - 明确输入/输出：每层只消费上层产物、产出本层产物
    - 与现有 UnifiedDataPool.MarketSnapshot 桥接（见 bridge.py），不破坏存量

层映射：
    L2 数据层   → MarketSnapshot
    L3 Alpha 层 → FactorVector → Insight
    L4 组合/风控 → Target → ApprovedTarget
    L5 执行层   → OrderEvent
"""
from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Optional


class Direction(str, Enum):
    LONG = "long"
    SHORT = "short"
    FLAT = "flat"


class Horizon(str, Enum):
    SCALP = "scalp"
    SHORT = "short"
    MID = "mid"
    LONG = "long"


class DataQuality(str, Enum):
    OK = "OK"
    STALE = "STALE"        # 数据迟到
    GAP = "GAP"            # L2 序列号缺口
    DEGRADED = "DEGRADED"  # 部分源 down


class OrderAlgo(str, Enum):
    MARKET = "MARKET"
    TWAP = "TWAP"
    POV = "POV"
    FUNDING_IS = "FUNDING_IS"
    SOR = "SOR"


class OrderUrgency(str, Enum):
    LOW = "LOW"
    NORMAL = "NORMAL"
    HIGH = "HIGH"


class OrderStatus(str, Enum):
    NEW = "NEW"
    FILLED = "FILLED"
    PARTIAL = "PARTIAL"
    CANCELED = "CANCELED"
    REJECTED = "REJECTED"


# ==================== L2 数据层 ====================

@dataclass(frozen=True)
class Instrument:
    """交易品种（不可变标识）。"""
    symbol: str          # 统一符号，如 "BTC-PERP"
    venue: str           # 交易所，如 "hyperliquid"
    kind: str            # perp / spot / option
    tick_size: float = 0.0
    lot_size: float = 0.0
    adv_usd: float = 0.0  # 日均成交额（容量/选品用）


@dataclass(frozen=True)
class MarketSnapshot:
    """
    L2 数据层输出。单一品种某时刻的市场快照。

    与现有 UnifiedDataPool.MarketSnapshot 的区别：
        - 含 L2 盘口 top-N（微观因子/执行用）
        - 含序列号（gap 检测）
        - 含数据质量标记（QualityGate 消费）
        - 不可变（事件溯源）
    """
    ts_ns: int                       # 单调纳秒时间戳
    instrument: Instrument
    bid: float
    ask: float
    mid: float
    last_trade: float
    last_trade_size: float
    l2: tuple[tuple[float, float], ...] = ()  # (price, size) top-N 档
    funding_rate: Optional[float] = None
    open_interest: Optional[float] = None
    seq: int = 0                     # L2 序列号（gap 检测用）
    quality: DataQuality = DataQuality.OK


# ==================== L3 Alpha 层 ====================

@dataclass(frozen=True)
class FactorVector:
    """FactorCompute 输出。单品种某时刻的全部活跃因子值。"""
    ts_ns: int
    instrument: Instrument
    values: dict[str, float] = field(default_factory=dict)  # factor_name -> value（已正交化、归一化）
    expr_ids: dict[str, str] = field(default_factory=dict)  # factor_name -> 表达式版本id（可追溯重算）


@dataclass(frozen=True)
class Insight:
    """
    AlphaEnsemble 输出 / Portfolio 输入。
    方向 + 置信度（来自 MetaLabel 副模型）+ 预期幅度 + 有效期。
    """
    ts_ns: int
    instrument: Instrument
    direction: Direction
    confidence: float                # 0..1，MetaLabel 副模型产出
    magnitude: float                # 预期收益幅度（仓位 sizing 用）
    period_ns: int                  # 预期持仓时间
    horizon: Horizon
    source: str                     # ensemble 哪个子模型主导（审计）
    expiry_ns: int                  # 信号有效期（过期自动作废，防慢执行当新信号）


# ==================== L4 组合/风控层 ====================

@dataclass(frozen=True)
class Target:
    """PortfolioConstruction 输出 / Risk 输入。目标仓位（带符号）。"""
    ts_ns: int
    instrument: Instrument
    target_qty: float               # 目标仓位（正=多，负=空）
    reason: str = ""
    algo: OrderAlgo = OrderAlgo.MARKET  # 执行算法偏好（RiskGate 透传到 ApprovedTarget）


@dataclass(frozen=True)
class ApprovedTarget:
    """Risk 输出 / Execution 输入。可能被风控削减。"""
    ts_ns: int
    instrument: Instrument
    approved_qty: float             # 可能 < target_qty（被风控削减）
    algo: OrderAlgo = OrderAlgo.MARKET
    urgency: OrderUrgency = OrderUrgency.NORMAL
    gate_log: tuple[str, ...] = ()  # 通过/被减的 gate 列表（审计）


# ==================== L5 执行层 ====================

@dataclass(frozen=True)
class OrderEvent:
    """Execution 输出。订单事件（事件溯源落盘）。"""
    ts_ns: int
    instrument: Instrument
    client_id: str
    venue_order_id: Optional[str]
    side: str                       # buy / sell
    price: Optional[float]
    qty: float
    status: OrderStatus
    fill_price: Optional[float] = None
    fill_qty: float = 0.0
    fee: float = 0.0
    ts_event_ns: int = 0            # 交易所事件时间


# ==================== 跨层辅助 ====================

@dataclass(frozen=True)
class RegimeLabel:
    """RegimeAgent 异步广播（不阻塞 tick）。"""
    ts_ns: int
    regime: str                     # 细分 regime（趋势高/低波/区间/逼空/连环清算/极端）
    confidence: float
    source: str = "regime_agent"


@dataclass(frozen=True)
class DataQualityFlag:
    """QualityGate 输出。迟到/缺口 → 降级/冻结信号。"""
    ts_ns: int
    instrument: Instrument
    quality: DataQuality
    detail: str = ""
