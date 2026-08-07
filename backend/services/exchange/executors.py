"""统一执行层接口 —— 实盘/模拟执行器的共享契约。

设计目标（阶段 3 执行层标准化）:
- 所有交易逻辑（策略生成、风控、订单构建）基于统一接口，不再为 paper 保留特殊简化逻辑
- 通道分离: PaperExecutor（模拟）/ LiveExecutor（实盘）实现同一接口
- 策略层（full_auto / ArbitrageOrchestrator）通过 ExecutionChannel 抽象切换通道，零改动

核心抽象:
    OrderContext  —— 统一下单上下文（symbol/side/size/leverage/tp/sl 等）
    OrderResult   —— 统一执行结果（status/order_id/fill_price/position_id/error）
    ExecutionChannel (ABC) —— 执行通道接口（place_order/get_positions/close_position/get_balance）

用法:
    from backend.services.exchange.executors import (
        OrderContext, OrderResult, ExecutionChannel,
        PaperExecutor, LiveExecutor, get_executor,
    )
    executor = get_executor(trading_mode="paper")  # 或 "live"
    result = executor.place_order(db, ctx)

注意: 本模块仅定义契约，具体实现在 paper_executor.py / live_executor.py。
"""
from __future__ import annotations

import logging
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)


# ────────────────────────────────────────────────────────────────────
# 统一下单上下文
# ────────────────────────────────────────────────────────────────────

@dataclass
class OrderContext:
    """统一下单上下文 —— 实盘/模拟共用。

    封装一笔交易的所有参数，屏蔽底层 paper_engine / HL native / CCXT 的签名差异。

    Attributes:
        account_id: 账户 ID（paper 模式为 paper_account_id，live 为 trader account_id）
        symbol: 交易对（如 "BTC"）
        side: 方向 "buy"/"sell"（开仓视角；paper_engine 内部映射 long/short）
        quantity: 数量（基础币种，如 0.1 BTC）
        order_type: "market" / "limit"
        price: 限价单价格（market 单可省略）
        leverage: 杠杆倍数
        tp_price: 止盈价（可选）
        sl_price: 止损价（可选）
        strategy_id: 策略 ID（paper 子仓隔离用）
        timeframe_tier: 时间框架 "short"/"mid"/"long"
        trade_nature: 交易性质 "scalp"/"swing"/"trend_follow" 等
        expected_hold_hours: 预期持仓时长
        reduce_only: 是否仅减仓（实盘用，paper 模式忽略）
        algo: 执行算法（MARKET/TWAP/POV/FUNDING_IS/SOR，阶段 3.2 接线）
        algo_config: 算法配置 dict（twap_slices / twap_interval_ms 等，可选）
        trigger_context: 实盘触发上下文（full_auto 传递 pre_made_decisions）
    """
    account_id: int
    symbol: str
    side: str  # "buy" / "sell"
    quantity: float
    order_type: str = "market"
    price: Optional[float] = None
    leverage: float = 1.0
    tp_price: Optional[float] = None
    sl_price: Optional[float] = None
    strategy_id: Optional[str] = None
    timeframe_tier: Optional[str] = None
    trade_nature: Optional[str] = None
    expected_hold_hours: Optional[float] = None
    reduce_only: bool = False
    algo: str = "MARKET"  # MARKET / TWAP / POV / FUNDING_IS / SOR
    algo_config: Optional[Dict[str, Any]] = None
    trigger_context: Optional[Dict[str, Any]] = None
    position_metadata: Optional[Dict[str, Any]] = None

    def to_paper_kwargs(self) -> Dict[str, Any]:
        """转换为 paper_engine.place_order 的 kwargs（去掉 account_id/symbol/side/quantity）。"""
        kw = {
            "order_type": self.order_type,
            "price": self.price,
            "leverage": self.leverage,
            "tp_price": self.tp_price,
            "sl_price": self.sl_price,
            "strategy_id": self.strategy_id,
            "timeframe_tier": self.timeframe_tier,
            "trade_nature": self.trade_nature,
            "expected_hold_hours": self.expected_hold_hours,
        }
        if self.position_metadata:
            kw["position_metadata"] = self.position_metadata
        return kw


# ────────────────────────────────────────────────────────────────────
# 统一执行结果
# ────────────────────────────────────────────────────────────────────

@dataclass
class OrderResult:
    """统一执行结果 —— 规范化 paper/live 的异构返回。

    status 取值:
    - "filled": 完全成交（成功）
    - "partial": 部分成交（实盘可能，paper 暂无）
    - "pending": 限价单挂单中（未成交）
    - "rejected": 拒单（余额不足/风控拦截/交易所拒单）
    - "blocked": 风控拦截（与 rejected 区分，便于统计）
    - "error": 异常（网络/代码错误）
    """
    status: str  # filled/partial/pending/rejected/blocked/error
    order_id: Optional[str] = None
    position_id: Optional[int] = None  # paper 模式有，live 无
    symbol: Optional[str] = None
    side: Optional[str] = None
    fill_price: Optional[float] = None
    filled_quantity: Optional[float] = None
    fee: Optional[float] = None
    leverage: Optional[float] = None
    tp_price: Optional[float] = None
    sl_price: Optional[float] = None
    pnl: Optional[float] = None  # 平仓时有
    channel: str = "unknown"  # "paper" / "live"
    exchange: Optional[str] = None
    raw: Optional[Dict[str, Any]] = None  # 原始返回（审计用）
    error: Optional[str] = None
    blocked_by: Optional[str] = None  # 风控规则名
    blocked_layer: Optional[str] = None  # 风控层级

    @property
    def success(self) -> bool:
        """是否成功（filled 或 pending 限价单）。"""
        return self.status in ("filled", "pending")

    @property
    def is_blocked(self) -> bool:
        """是否被风控拦截。"""
        return self.status == "blocked"

    def to_dict(self) -> Dict[str, Any]:
        return {
            "status": self.status,
            "success": self.success,
            "order_id": self.order_id,
            "position_id": self.position_id,
            "symbol": self.symbol,
            "side": self.side,
            "fill_price": self.fill_price,
            "filled_quantity": self.filled_quantity,
            "fee": self.fee,
            "leverage": self.leverage,
            "channel": self.channel,
            "exchange": self.exchange,
            "error": self.error,
            "blocked_by": self.blocked_by,
        }


# ────────────────────────────────────────────────────────────────────
# 执行通道抽象基类
# ────────────────────────────────────────────────────────────────────

class ExecutionChannel(ABC):
    """执行通道抽象基类 —— PaperExecutor / LiveExecutor 实现此接口。

    所有方法 sync（与现有 paper_engine / trading_commands 一致）。
    """

    @property
    @abstractmethod
    def channel_name(self) -> str:
        """通道名 "paper" / "live"。"""
        ...

    @abstractmethod
    def place_order(self, db, ctx: OrderContext) -> OrderResult:
        """下单（开仓/加仓）。"""
        ...

    @abstractmethod
    def close_position(
        self, db, account_id: int, symbol: str, side: str,
        reason: str = "manual", quantity: Optional[float] = None,
        strategy_id: Optional[str] = None,
    ) -> OrderResult:
        """平仓。

        Args:
            side: 持仓方向 "long"/"short"（注意：是仓位方向，非订单方向）
        """
        ...

    @abstractmethod
    def get_positions(self, db, account_id: int, status: str = "open") -> List[Dict[str, Any]]:
        """查询持仓列表。"""
        ...

    @abstractmethod
    def get_balance(self, db, account_id: int) -> Optional[Dict[str, Any]]:
        """查询账户余额。"""
        ...


# ────────────────────────────────────────────────────────────────────
# 执行器工厂
# ────────────────────────────────────────────────────────────────────

def get_executor(trading_mode: str = "paper", exchange: Optional[str] = None) -> ExecutionChannel:
    """根据交易模式获取执行器。

    Args:
        trading_mode: "paper" / "live"（其他值视为 paper）
        exchange: 交易所名（live 模式路由用，paper 模式忽略）

    Returns:
        ExecutionChannel 实例（PaperExecutor 或 LiveExecutor）

    开关: USE_UNIFIED_EXECUTOR=false 时仍可用，但调用方应回退到旧路径。
    本函数本身不读开关（开关在 full_auto 调用点判断）。
    """
    mode = (trading_mode or "paper").lower().strip()
    if mode == "live":
        from backend.services.exchange.live_executor import LiveExecutor
        return LiveExecutor(exchange=exchange)
    # 默认 paper
    from backend.services.exchange.paper_executor import PaperExecutor
    return PaperExecutor(exchange=exchange)


def is_unified_executor_enabled() -> bool:
    """读取 USE_UNIFIED_EXECUTOR 开关（默认 false，渐进启用）。

    阶段 3 灰度策略: 默认关闭，验证后开 true。
    false 时 full_auto 回退到旧的 _execute_paper_trade / _execute_live_trade 直接调用。
    """
    import os
    return os.getenv("USE_UNIFIED_EXECUTOR", "false").lower().strip() in ("true", "1", "yes", "on")
