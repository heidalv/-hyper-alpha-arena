"""
CrossExchangeArbitrageEngine — 跨交易所价差套利引擎

策略：当价差偏离历史均值超过2σ时开仓，回归时平仓。
设计文档: SYSTEM_UPGRADE_DESIGN_V3.md 第3.4节
"""

import logging
from dataclasses import dataclass, field
from datetime import datetime
from typing import Dict, List, Optional

import numpy as np

from backend.services.exchange.base_exchange_client import (
    BaseExchangeClient,
    ExchangeOrder,
    OrderSide,
    OrderType,
)

logger = logging.getLogger(__name__)


@dataclass
class CrossExchangeSpread:
    """跨交易所价差"""
    symbol: str
    exchange_a: str
    exchange_b: str
    price_a: float
    price_b: float
    spread_pct: float           # (price_a - price_b) / avg_price * 100
    historical_mean: float      # 历史平均价差
    historical_std: float       # 历史价差标准差
    z_score: float              # 当前价差Z-Score
    timestamp: datetime = field(default_factory=datetime.now)

    @property
    def direction(self) -> str:
        """价差方向: a_above_b 或 a_below_b"""
        return "a_above_b" if self.spread_pct > 0 else "a_below_b"


@dataclass
class CrossExchangeTrade:
    """跨交易所套利交易记录"""
    trade_id: str
    symbol: str
    spread: CrossExchangeSpread
    side_a: OrderSide           # 交易所A的方向
    side_b: OrderSide           # 交易所B的方向
    size: float
    entry_spread_pct: float
    status: str = "open"        # open / closed / failed
    exit_spread_pct: Optional[float] = None
    pnl: Optional[float] = None
    timestamp: datetime = field(default_factory=datetime.now)


class CrossExchangeArbitrageEngine:
    """
    跨交易所套利引擎

    策略逻辑：
    1. 扫描两个交易所的订单簿价差
    2. 计算价差的Z-Score（基于历史均值和标准差）
    3. Z-Score超过阈值时开仓（买便宜的一侧，卖贵的一侧）
    4. Z-Score回归时平仓
    """

    SPREAD_ENTRY_ZSCORE = 2.0       # 开仓阈值
    SPREAD_EXIT_ZSCORE = 0.5        # 平仓阈值
    MAX_POSITION_PCT = 0.15         # 单笔最大15%
    SPREAD_HISTORY_WINDOW = 168     # 7天历史（小时数据）
    MIN_HISTORY_FOR_STATS = 5       # 计算统计值的最小历史数据量

    def __init__(
        self,
        client_a: BaseExchangeClient,
        client_b: BaseExchangeClient,
    ):
        self.client_a = client_a
        self.client_b = client_b
        self._spread_history: Dict[str, List[float]] = {}
        self._active_trades: Dict[str, CrossExchangeTrade] = {}
        self._trade_counter = 0

    async def scan_spreads(
        self, symbols: List[str]
    ) -> List[CrossExchangeSpread]:
        """扫描跨交易所价差"""
        spreads = []
        from backend.services.arbitrage.cross_exchange_mid_cache import mid_cache

        ex_a_name = self.client_a.exchange_type.value
        ex_b_name = self.client_b.exchange_type.value

        for symbol in symbols:
            try:
                cached_a = mid_cache.get_mid(ex_a_name, symbol)
                cached_b = mid_cache.get_mid(ex_b_name, symbol)

                if cached_a and cached_b:
                    mid_a = cached_a.mid_price
                    mid_b = cached_b.mid_price
                else:
                    book_a = await self.client_a.get_orderbook(symbol, depth=5)
                    book_b = await self.client_b.get_orderbook(symbol, depth=5)

                    bids_a = book_a.get('bids', [])
                    asks_a = book_a.get('asks', [])
                    bids_b = book_b.get('bids', [])
                    asks_b = book_b.get('asks', [])

                    if not bids_a or not asks_a or not bids_b or not asks_b:
                        continue

                    mid_a = mid_cache.refresh_from_orderbook(ex_a_name, symbol, book_a)
                    mid_b = mid_cache.refresh_from_orderbook(ex_b_name, symbol, book_b)
                    if mid_a is None or mid_b is None:
                        mid_a = (float(bids_a[0][0]) + float(asks_a[0][0])) / 2
                        mid_b = (float(bids_b[0][0]) + float(asks_b[0][0])) / 2
                avg = (mid_a + mid_b) / 2
                if avg <= 0:
                    continue
                spread_pct = (mid_a - mid_b) / avg * 100

                # 更新历史
                key = f"{symbol}_{self.client_a.exchange_type.value}_{self.client_b.exchange_type.value}"
                if key not in self._spread_history:
                    self._spread_history[key] = []
                self._spread_history[key].append(spread_pct)

                hist = self._spread_history[key][-self.SPREAD_HISTORY_WINDOW:]
                mean = float(np.mean(hist))
                std = float(np.std(hist)) + 1e-10
                z = float((spread_pct - mean) / std)

                spreads.append(CrossExchangeSpread(
                    symbol=symbol,
                    exchange_a=self.client_a.exchange_type.value,
                    exchange_b=self.client_b.exchange_type.value,
                    price_a=mid_a,
                    price_b=mid_b,
                    spread_pct=spread_pct,
                    historical_mean=mean,
                    historical_std=std,
                    z_score=z,
                ))
            except Exception as e:
                logger.debug("scan_spreads error for %s: %s", symbol, e)
                continue
        return spreads

    def find_entry_opportunities(
        self, spreads: List[CrossExchangeSpread]
    ) -> List[CrossExchangeSpread]:
        """从扫描结果中筛选开仓机会"""
        opportunities = []
        for spread in spreads:
            if len(self._spread_history.get(
                f"{spread.symbol}_{spread.exchange_a}_{spread.exchange_b}", []
            )) < self.MIN_HISTORY_FOR_STATS:
                continue
            if abs(spread.z_score) >= self.SPREAD_ENTRY_ZSCORE:
                opportunities.append(spread)
        return opportunities

    def find_exit_opportunities(
        self, spreads: List[CrossExchangeSpread]
    ) -> List[tuple]:
        """从扫描结果中筛选平仓机会，返回 (trade, spread) 列表"""
        exits = []
        current_spreads = {s.symbol: s for s in spreads}
        for trade_id, trade in self._active_trades.items():
            if trade.symbol in current_spreads:
                current = current_spreads[trade.symbol]
                if abs(current.z_score) <= self.SPREAD_EXIT_ZSCORE:
                    exits.append((trade, current))
        return exits

    def generate_trade_orders(
        self, spread: CrossExchangeSpread, equity: float, size: Optional[float] = None
    ) -> tuple:
        """
        根据价差方向生成配对订单

        Returns:
            (order_a, order_b): 两个交易所的配对订单
        """
        if size is None:
            size = equity * self.MAX_POSITION_PCT / (spread.price_a + spread.price_b) * 2
            size = max(size, 0.001)

        self._trade_counter += 1
        trade_id = f"xea_{self._trade_counter}"

        if spread.z_score > 0:
            # A贵B便宜 → 在A卖出，在B买入
            side_a = OrderSide.SELL
            side_b = OrderSide.BUY
        else:
            # A便宜B贵 → 在A买入，在B卖出
            side_a = OrderSide.BUY
            side_b = OrderSide.SELL

        order_a = ExchangeOrder(
            order_id=f"{trade_id}_a",
            symbol=spread.symbol,
            side=side_a,
            order_type=OrderType.MARKET,
            size=size,
        )
        order_b = ExchangeOrder(
            order_id=f"{trade_id}_b",
            symbol=spread.symbol,
            side=side_b,
            order_type=OrderType.MARKET,
            size=size,
        )

        trade = CrossExchangeTrade(
            trade_id=trade_id,
            symbol=spread.symbol,
            spread=spread,
            side_a=side_a,
            side_b=side_b,
            size=size,
            entry_spread_pct=spread.spread_pct,
        )
        self._active_trades[trade_id] = trade

        return order_a, order_b

    def close_trade(self, trade_id: str, exit_spread_pct: float) -> Optional[CrossExchangeTrade]:
        """平仓并记录盈亏"""
        if trade_id not in self._active_trades:
            return None
        trade = self._active_trades.pop(trade_id)
        trade.status = "closed"
        trade.exit_spread_pct = exit_spread_pct
        # PnL = (入场价差 - 出场价差) * size / 100
        trade.pnl = (trade.entry_spread_pct - exit_spread_pct) * trade.size / 100
        return trade

    def get_active_trades(self) -> Dict[str, CrossExchangeTrade]:
        """获取活跃交易"""
        return dict(self._active_trades)

    def get_spread_history(self, symbol: str) -> List[float]:
        """获取价差历史"""
        key = f"{symbol}_{self.client_a.exchange_type.value}_{self.client_b.exchange_type.value}"
        return list(self._spread_history.get(key, []))

    @property
    def trade_count(self) -> int:
        """累计交易次数"""
        return self._trade_counter
