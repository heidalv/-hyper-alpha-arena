"""
返利/积分套利引擎数据模型

定义返利套利引擎的核心数据结构：
- RebateStrategyType: 策略类型枚举 (S1-S8)
- RebatePosition: 返利仓位
- RebateRiskResult: 风控检查结果
- WashTradeCheckResult: 刷量检测结果
- CapitalAllocation: 资金分配状态
- RebateExecutionResult: 执行结果
"""

from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Dict, List, Optional


class RebateStrategyType(Enum):
    """返利套利策略类型"""
    S1_MAKER_HEDGE = "S1"
    S2_VIP_SPRINT = "S2"
    S3_POINTS_MINING = "S3"
    S4_CAMPAIGN_ARB = "S4"
    S5_FUNDING_POINTS = "S5"
    S6_CROSS_FEE_SPREAD = "S6"
    S7_BINANCE_ALPHA = "S7"
    S8_ASTERDEX_RH = "S8"
    # 2026-07-06 新增（Phase 2）：delta-neutral 刷积分核心策略——
    # 在 active 积分 DEX 开多、深流动性场所开等额空，对冲方向风险，
    # 赚资金费价差 + 白拿积分。是 2026 主流刷分范式的载体。
    SDN_DELTA_NEUTRAL = "SDN"


class RebatePositionStatus(Enum):
    """仓位状态"""
    PENDING = "pending"
    ACTIVE = "active"
    CLOSING = "closing"
    CLOSED = "closed"
    ERROR = "error"


class RiskCheckAction(Enum):
    """风控动作"""
    PASS = "pass"
    WARN = "warn"
    BLOCK = "block"
    EMERGENCY_STOP = "emergency_stop"


@dataclass
class RebatePosition:
    """返利套利仓位"""
    position_id: str
    strategy_type: RebateStrategyType
    source_exchange: str
    target_exchange: Optional[str]
    symbol: str
    side_a_size: float = 0.0          # A腿仓位大小 (USD)
    side_b_size: float = 0.0          # B腿仓位大小 (USD)
    entry_price_a: float = 0.0
    entry_price_b: float = 0.0
    current_pnl: float = 0.0
    accumulated_rebate: float = 0.0   # 累计返利收益
    accumulated_points: float = 0.0   # 累计积分
    entry_time: float = 0.0
    max_hold_seconds: float = 86400 * 30  # 最大持仓30天
    status: RebatePositionStatus = RebatePositionStatus.ACTIVE
    paper_mode: bool = True
    metadata: Dict[str, Any] = field(default_factory=dict)

    @property
    def total_size(self) -> float:
        return self.side_a_size + self.side_b_size

    @property
    def net_cost(self) -> float:
        """净成本（扣除返利后的费用）"""
        return self.current_pnl + self.accumulated_rebate

    @property
    def hold_duration_hours(self) -> float:
        import time
        return (time.time() - self.entry_time) / 3600


@dataclass
class RiskCheckResult:
    """风控检查结果"""
    passed: bool
    action: RiskCheckAction = RiskCheckAction.PASS
    rule_id: str = ""
    reason: str = ""
    details: Dict[str, Any] = field(default_factory=dict)

    @property
    def is_blocking(self) -> bool:
        return self.action in (RiskCheckAction.BLOCK, RiskCheckAction.EMERGENCY_STOP)


@dataclass
class WashTradeCheckResult:
    """刷量检测结果"""
    is_safe: bool
    risk_score: float = 0.0          # 0~1, 越高越危险
    layer_results: Dict[str, bool] = field(default_factory=dict)
    recommendation: str = ""
    next_safe_ts: float = 0.0        # 下一个安全交易时间戳

    @property
    def risk_level(self) -> str:
        if self.risk_score < 0.3:
            return "low"
        elif self.risk_score < 0.7:
            return "medium"
        return "high"


@dataclass
class CapitalAllocation:
    """资金池分配状态"""
    total_equity: float = 0.0
    allocations: Dict[str, float] = field(default_factory=dict)  # pool_name -> allocated_usd
    used: Dict[str, float] = field(default_factory=dict)         # pool_name -> used_usd
    locked: bool = False

    @property
    def available_for_rebate(self) -> float:
        """返利池可用资金"""
        allocated = self.allocations.get("rebate_points_arb", 0.0)
        used = self.used.get("rebate_points_arb", 0.0)
        return max(allocated - used, 0.0)

    @property
    def total_utilization(self) -> float:
        """总资金利用率"""
        total_allocated = sum(self.allocations.values())
        total_used = sum(self.used.values())
        return total_used / max(total_allocated, 1.0)


@dataclass
class StrategyEvaluation:
    """策略评估结果"""
    strategy_type: RebateStrategyType
    is_viable: bool
    expected_monthly_value: float = 0.0
    required_volume_usd: float = 0.0
    risk_score: float = 0.0
    confidence: float = 0.0
    details: Dict[str, Any] = field(default_factory=dict)

    @property
    def volume_value_ratio(self) -> float:
        return self.expected_monthly_value / max(self.required_volume_usd, 1.0)


@dataclass
class RebateExecutionResult:
    """执行结果"""
    success: bool
    position_id: str = ""
    strategy_type: Optional[RebateStrategyType] = None
    side_a_order: Optional[Dict] = None
    side_b_order: Optional[Dict] = None
    error: str = ""
    paper_mode: bool = True
