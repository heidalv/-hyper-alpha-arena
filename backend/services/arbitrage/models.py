"""
套利引擎数据结构

定义资金费率套利引擎的所有数据模型，包括：
- ArbitrageStatus: 套利状态枚举
- FundingRateSnapshot: 资金费率快照
- HedgePosition: 对冲头寸
- ArbitrageOpportunity: 套利机会
- HedgeRiskCheckResult: 对冲风控检查结果
"""

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


@dataclass
class FundingRateSnapshot:
    """资金费率快照"""
    symbol: str
    current_rate: float          # 当前资金费率
    predicted_rate: float        # 预测下一期费率
    rate_8h_avg: float           # 8小时平均（最近3期）
    rate_24h_avg: float          # 24小时平均（最近9期）
    annual_yield: float          # 年化收益率 = rate_24h_avg * 3 * 365
    oi_total: float              # 持仓量(USD)
    volume_24h: float            # 24h成交量
    timestamp: float = 0.0

    @property
    def is_extreme(self) -> bool:
        """费率是否处于极端水平"""
        return abs(self.current_rate) > 0.01  # > 1%


@dataclass
class HedgePosition:
    """对冲头寸"""
    position_id: str
    symbol: str
    long_size: float             # 多头仓位大小
    long_entry_price: float
    short_size: float            # 空头仓位大小
    short_entry_price: float
    delta: float                 # 净敞口 = long_value - short_value
    accumulated_funding: float   # 累计资金费率收益
    entry_time: float            # 入场时间(Unix)
    status: ArbitrageStatus = ArbitrageStatus.ACTIVE
    funding_payments_count: int = 0

    @property
    def is_balanced(self) -> bool:
        """头寸是否平衡（delta < 2%）"""
        return abs(self.delta) / max(self.long_size, self.short_size, 1e-10) < 0.02


@dataclass
class ArbitrageOpportunity:
    """套利机会"""
    opportunity_id: str
    symbol: str
    strategy: str                # "funding_long" / "funding_short"
    expected_annual_yield: float
    funding_snapshot: FundingRateSnapshot
    recommended_size: float      # 建议仓位大小(USD)
    risk_score: float            # 0~1, 越低越好
    confidence: float            # 0~1
    timestamp: float = 0.0
    status: ArbitrageStatus = ArbitrageStatus.ACTIVE


@dataclass
class HedgeRiskCheckResult:
    """对冲风控检查结果"""
    passed: bool
    reason_code: str = ""
    reason_text: str = ""
    blocked_by: str = ""
