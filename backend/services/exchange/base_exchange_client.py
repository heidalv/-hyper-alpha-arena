"""
BaseExchangeClient — 交易所客户端抽象基类

定义统一的交易所接口，所有交易所适配器必须实现。
设计文档: SYSTEM_UPGRADE_DESIGN_V3.md 第3.3.1节
"""

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional
from enum import Enum


class OrderSide(Enum):
    BUY = "buy"
    SELL = "sell"


class OrderType(Enum):
    MARKET = "market"
    LIMIT = "limit"


class ExchangeType(Enum):
    HYPERLIQUID = "hyperliquid"
    BINANCE = "binance"
    BYBIT = "bybit"
    OKX = "okx"
    GATEIO = "gateio"
    ASTERDEX = "asterdex"


@dataclass
class ExchangeOrder:
    """统一订单结构"""
    order_id: str
    symbol: str
    side: OrderSide
    order_type: OrderType
    size: float
    price: Optional[float] = None
    sl: Optional[float] = None
    tp: Optional[float] = None
    leverage: int = 1
    reduce_only: bool = False

    @property
    def notional_value(self) -> float:
        """订单名义价值"""
        return self.size * (self.price or 0)


@dataclass
class ExchangePosition:
    """统一仓位结构"""
    symbol: str
    side: str
    size: float
    entry_price: float
    mark_price: float
    unrealized_pnl: float
    margin: float
    leverage: float
    liquidation_price: Optional[float] = None

    @property
    def notional_value(self) -> float:
        """仓位名义价值"""
        return self.size * self.mark_price


@dataclass
class ExchangeBalance:
    """统一余额结构"""
    total_equity: float
    available_balance: float
    frozen_margin: float
    unrealized_pnl: float

    @property
    def margin_ratio(self) -> float:
        """保证金使用率"""
        if self.total_equity <= 0:
            return 0.0
        return self.frozen_margin / self.total_equity


# ── 积分/返利套利相关数据结构 ──


@dataclass
class ExchangeFeeTier:
    """交易所费率等级"""
    exchange: str
    tier_name: str
    maker_rate: float
    taker_rate: float
    rebate_rate: float = 0.0
    volume_30d_usd: float = 0.0
    next_tier_volume: float = 0.0

    @property
    def effective_taker_cost(self) -> float:
        """有效 Taker 成本（扣除返佣后）"""
        return self.taker_rate * (1 - self.rebate_rate)

    @property
    def net_maker_rate(self) -> float:
        """净 Maker 费率（扣除返佣后）"""
        return self.maker_rate * (1 - self.rebate_rate)


@dataclass
class ExchangePointsSnapshot:
    """交易所积分快照"""
    exchange: str
    points_balance: float = 0.0
    points_multiplier: float = 1.0
    season: str = ""
    epoch: int = 0
    daily_points_rate: float = 0.0
    qualifying_days: int = 0
    required_days: int = 2
    airdrop_eligible: bool = False
    estimated_airdrop_value: float = 0.0

    @property
    def qualification_pct(self) -> float:
        """达标百分比"""
        return min(self.qualifying_days / max(self.required_days, 1), 1.0)


@dataclass
class ExchangeRebateInfo:
    """交易所返利配置"""
    exchange: str
    base_rebate_rate: float = 0.0
    current_rebate_rate: float = 0.0
    stacked_multiplier: float = 1.0
    trading_volume_7d: float = 0.0
    projected_weekly_rebate: float = 0.0
    points_from_fee: float = 0.0


@dataclass
class ExchangeIncentiveSummary:
    """交易所激励政策汇总"""
    exchange: str
    exchange_type: ExchangeType
    fee_tier: ExchangeFeeTier
    points: ExchangePointsSnapshot
    rebate: ExchangeRebateInfo
    is_connected: bool = True
    last_update: float = 0.0

    @property
    def total_estimated_monthly_value(self) -> float:
        """预估月总激励价值"""
        return (
            self.rebate.projected_weekly_rebate * 4
            + self.points.estimated_airdrop_value / 3
        )


@dataclass
class IncentiveArbitrageOpportunity:
    """积分/返利套利机会"""
    opportunity_id: str
    strategy_type: str  # S1-S8
    source_exchange: str
    target_exchange: Optional[str] = None
    expected_monthly_value: float = 0.0
    required_volume_usd: float = 0.0
    risk_score: float = 0.0
    confidence: float = 0.0
    status: str = "open"
    season_deadline: float = 0.0

    @property
    def volume_value_ratio(self) -> float:
        """价值比 = 月期望价值 / 所需交易量"""
        return self.expected_monthly_value / max(self.required_volume_usd, 1.0)


@dataclass
class ExchangeTrade:
    """统一逐笔成交结构 — 供 CVD / 主动买卖盘 / 订单流分析使用"""
    timestamp: int          # 毫秒时间戳
    symbol: str             # 统一 symbol（如 "BTC"，不含交易所后缀）
    price: float
    size: float             # 成交数量（基础币种）
    side: str               # "buy" = 主动买（吃 ask） / "sell" = 主动卖（吃 bid）
    taker_or_maker: str = "taker"   # 默认按 taker（CVD 主要关心 taker 方向）

    @property
    def notional(self) -> float:
        """名义价值 = 价格 × 数量"""
        return self.price * self.size

    @property
    def is_taker_buy(self) -> bool:
        """是否为主动买入（CVD 正向贡献）"""
        return self.side == "buy"


class BaseExchangeClient(ABC):
    """
    交易所客户端抽象基类
    所有交易所适配器必须实现此接口
    """

    @property
    @abstractmethod
    def exchange_type(self) -> ExchangeType:
        """交易所类型"""
        pass

    @property
    @abstractmethod
    def supports_spot(self) -> bool:
        """是否支持现货"""
        pass

    @property
    @abstractmethod
    def supports_futures(self) -> bool:
        """是否支持合约"""
        pass

    @abstractmethod
    async def get_balance(self) -> ExchangeBalance:
        """获取账户余额"""
        pass

    @abstractmethod
    async def get_positions(self) -> List[ExchangePosition]:
        """获取所有仓位"""
        pass

    @abstractmethod
    async def place_order(self, order: ExchangeOrder) -> Dict:
        """下单"""
        pass

    @abstractmethod
    async def cancel_order(self, order_id: str, symbol: str) -> bool:
        """取消订单"""
        pass

    @abstractmethod
    async def get_funding_rate(self, symbol: str) -> float:
        """获取单个币种资金费率"""
        pass

    @abstractmethod
    async def get_all_funding_rates(self) -> Dict[str, float]:
        """获取所有币种资金费率"""
        pass

    @abstractmethod
    async def get_orderbook(self, symbol: str, depth: int = 20) -> Dict:
        """获取订单簿"""
        pass

    @abstractmethod
    async def get_klines(
        self, symbol: str, interval: str, limit: int = 100
    ) -> List[Dict]:
        """获取K线数据"""
        pass

    # ── 积分/返利套利扩展方法 ──

    @abstractmethod
    async def get_fee_tier(self) -> "ExchangeFeeTier":
        """获取当前费率等级"""
        pass

    @abstractmethod
    async def get_points_snapshot(self) -> "ExchangePointsSnapshot":
        """获取积分快照（余额/乘数/空投资格）"""
        pass

    @abstractmethod
    async def get_rebate_info(self) -> "ExchangeRebateInfo":
        """获取返利配置（基础返利率/当前返利率/交易量）"""
        pass

    @abstractmethod
    async def get_incentive_summary(self) -> "ExchangeIncentiveSummary":
        """获取激励政策汇总（费率+积分+返利组合）"""
        pass

    @abstractmethod
    async def get_active_campaigns(self) -> List[Dict]:
        """获取进行中的竞赛/活动列表"""
        pass

    # ── 市场流 / CVD 扩展方法（订阅式） ──
    # 这些方法用非抽象默认实现抛 NotImplementedError，允许各适配器按需逐步覆盖，
    # 而不会破坏尚未实现该能力的现有交易所适配器（binance/bybit/okx/gateio/asterdex）。

    async def subscribe_trades(
        self,
        symbols: List[str],
        on_trade,
    ) -> Any:
        """
        订阅逐笔成交流（WS 优先，REST 降级由子类决定）。

        Args:
            symbols: 统一 symbol 列表（如 ["BTC", "ETH"]）
            on_trade: 回调，签名 async (ExchangeTrade) -> None 或 (ExchangeTrade) -> None

        Returns:
            订阅句柄（具体类型由子类决定，传给 unsubscribe 用）

        Note: 默认未实现。支持 CVD 采集的交易所（asterdex/hyperliquid）应覆盖此方法。
        """
        raise NotImplementedError(
            f"{self.__class__.__name__} 未实现 subscribe_trades（不支持 CVD 实时采集）"
        )

    async def subscribe_orderbook_stream(
        self,
        symbols: List[str],
        on_book,
    ) -> Any:
        """
        订阅 L2 订单簿流（统一接口，替代各处散落的 ccxt watch_order_book 直调）。

        Args:
            symbols: 统一 symbol 列表
            on_book: 回调，签名 async (exchange, symbol, book) -> None

        Returns:
            订阅句柄
        """
        raise NotImplementedError(
            f"{self.__class__.__name__} 未实现 subscribe_orderbook_stream"
        )

    async def fetch_recent_trades(
        self, symbol: str, limit: int = 500
    ) -> List["ExchangeTrade"]:
        """
        拉取最近成交（用于 WS 断线后的 replay 补数）。

        Args:
            symbol: 统一 symbol
            limit: 返回条数上限

        Returns:
            ExchangeTrade 列表（按时间升序）
        """
        raise NotImplementedError(
            f"{self.__class__.__name__} 未实现 fetch_recent_trades（不支持断线补数）"
        )

    async def unsubscribe(self, handle: Any) -> None:
        """取消订阅（句柄由 subscribe_* 返回）。默认实现无操作。"""
        return None
