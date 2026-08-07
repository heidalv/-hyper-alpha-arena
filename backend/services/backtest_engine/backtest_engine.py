"""
ATAS V2 回测引擎核心模块

提供向量化和事件驱动两种回测模式

Phase 3B §8.2 真实成本模型（方案修复）：
  - TAKER_FEE = 0.00035（HyperLiquid taker 0.035%）
  - MAKER_FEE = 0.0002（HyperLiquid maker 0.02%）
  - 回测保守估算：全部按 taker 费率计算
  - 滑点：基于订单大小占日成交量比例动态计算（非固定值）
"""
import os
import warnings
from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from typing import Dict, List, Optional, Any, Callable
import pandas as pd
import numpy as np
from abc import ABC, abstractmethod


class BacktestMode(Enum):
    """回测模式"""
    VECTORIZED = "vectorized"  # 向量化回测（快速）
    EVENT_DRIVEN = "event_driven"  # 事件驱动回测（精确）


# Phase 3B §8.2：HyperLiquid 真实手续费率
TAKER_FEE = 0.00035   # 0.035%
MAKER_FEE = 0.0002    # 0.02%
DEFAULT_SLIPPAGE = 0.0003   # 基础滑点 0.03%（主流币，保守估计）


def _legacy_calculate_slippage(order_size_usd: float, daily_volume_usd: float = 100_000_000) -> float:
    """[旧模型，仅回退用] 基于订单占日成交量比例的线性滑点（base 0.0003）。

    仅当 BACKTEST_SLIPPAGE_UNIFIED=false 时启用，用于一键回退到统一改造前的行为。
    """
    IMPACT_FACTOR = 0.1
    volume_ratio = order_size_usd / daily_volume_usd if daily_volume_usd > 0 else 1.0
    market_impact = volume_ratio * IMPACT_FACTOR
    return DEFAULT_SLIPPAGE + market_impact


def _unified_slippage_rate(
    notional_usd: float,
    trade_nature: str = "swing",
    is_sl: bool = False,
) -> float:
    """回测滑点单一真相源（2026-07-09 P0-4 统一）。

    默认委托实盘正在使用的 fee_guard.calc_slippage_rate（分级 size-adjusted，base 0.0005），
    使回测与实盘滑点口径完全同源，杜绝"回测用一套、实盘用另一套"的成本脱节。
    设 BACKTEST_SLIPPAGE_UNIFIED=false 可一键回退到旧的线性 volume-ratio 模型。
    """
    if os.getenv("BACKTEST_SLIPPAGE_UNIFIED", "true").lower() in ("1", "true", "yes", "on"):
        from backend.services.fee_guard import calc_slippage_rate
        return calc_slippage_rate(notional_usd, trade_nature, is_sl=is_sl)
    return _legacy_calculate_slippage(notional_usd)


def calculate_slippage(order_size_usd: float, daily_volume_usd: float = 100_000_000) -> float:
    """[已弃用] 滑点已统一到 fee_guard.calc_slippage_rate（实盘同源，见 _unified_slippage_rate）。

    保留此壳仅为向后兼容旧调用；现按统一口径委托，daily_volume_usd 参数被忽略。
    """
    warnings.warn(
        "calculate_slippage 已弃用：滑点已统一到 fee_guard.calc_slippage_rate（实盘同源），"
        "请改用 _unified_slippage_rate；daily_volume_usd 参数已被忽略。",
        DeprecationWarning,
        stacklevel=2,
    )
    return _unified_slippage_rate(order_size_usd, trade_nature="swing", is_sl=False)


def apply_funding_cost(
    position_value: float,
    funding_rate: float,
    hours_held: float,
) -> float:
    """
    计算持仓期间的资金费率成本（方案§8.2 (3)）。
    HyperLiquid 每 8 小时结算一次。
    
    Returns:
        总资金费率成本（美元）
    """
    funding_periods = hours_held / 8.0
    return position_value * abs(funding_rate) * funding_periods


@dataclass
class BacktestConfig:
    """回测配置（Phase 3B §8.2 真实成本模型）"""
    initial_capital: float = 100000.0
    # Phase 3B：改用真实 HyperLiquid taker 费率，替代原 0.001（0.1%）
    commission: float = TAKER_FEE       # 0.035% taker（保守全按 taker）
    slippage: float = DEFAULT_SLIPPAGE  # 0.03% 基础滑点（主流币）
    # 是否启用动态滑点（按订单量计算）
    use_dynamic_slippage: bool = False
    daily_volume_usd: float = 100_000_000   # 默认日成交量（动态滑点用）
    # 是否扣除资金费率
    apply_funding_rate: bool = False
    avg_funding_rate: float = 0.0001       # 默认资金费率（0.01%/8h）
    mode: BacktestMode = BacktestMode.VECTORIZED
    
    # 风险管理参数
    max_position_size: float = 0.95
    max_drawdown_limit: Optional[float] = None
    stop_loss_pct: Optional[float] = None
    take_profit_pct: Optional[float] = None
    
    # 性能参数
    enable_cache: bool = True
    parallel: bool = False

    # ===== 整改 #2：子 K 线回测 / 成交价模型（对标 Freqtrade --timeframe-detail）=====
    # 三者默认值均等价于"改造前行为"，通过环境变量一键开启，零风险回退。
    #   BACKTEST_INTRABAR_RESOLUTION=true  → 用 bar high/low 穿透判定 SL/TP（无 detail 数据时的保守法）
    #   BACKTEST_FILL_MODEL=next_open      → 信号在"下一根开盘"成交（防前视），默认 close 保持旧行为
    #   timeframe_detail='1m'              → 提供更细粒度子 K 线时，逐根扫描精确判定 SL/TP 谁先触及
    intrabar_resolution: bool = field(
        default_factory=lambda: os.getenv("BACKTEST_INTRABAR_RESOLUTION", "false").lower()
        in ("1", "true", "yes", "on")
    )
    fill_model: str = field(
        default_factory=lambda: os.getenv("BACKTEST_FILL_MODEL", "close")
    )
    timeframe_detail: Optional[str] = None


@dataclass
class Trade:
    """交易记录"""
    timestamp: datetime
    symbol: str
    side: str  # 'buy' or 'sell'
    price: float
    quantity: float
    commission: float
    slippage: float
    pnl: Optional[float] = None  # 盈亏（平仓时填充）


@dataclass
class Position:
    """持仓信息"""
    symbol: str
    quantity: float  # 持仓数量（正数=多头，负数=空头）
    entry_price: float  # 入场价格
    current_price: float  # 当前价格
    unrealized_pnl: float = 0.0  # 未实现盈亏
    realized_pnl: float = 0.0  # 已实现盈亏


@dataclass
class BacktestResult:
    """回测结果"""
    # 基本信息
    start_date: datetime
    end_date: datetime
    initial_capital: float
    final_capital: float
    
    # 收益指标
    total_return: float  # 总收益率
    annualized_return: float  # 年化收益率
    
    # 风险指标
    max_drawdown: float  # 最大回撤
    sharpe_ratio: float  # 夏普比率
    sortino_ratio: float  # 索提诺比率
    calmar_ratio: float  # 卡玛比率
    
    # 交易统计
    total_trades: int  # 总交易次数
    winning_trades: int  # 盈利交易次数
    losing_trades: int  # 亏损交易次数
    win_rate: float  # 胜率
    
    # 详细数据
    equity_curve: pd.Series  # 权益曲线
    trades: List[Trade]  # 交易记录
    positions: List[Position]  # 持仓历史
    
    # 额外统计
    avg_win: float = 0.0  # 平均盈利
    avg_loss: float = 0.0  # 平均亏损
    profit_factor: float = 0.0  # 盈亏比
    max_consecutive_wins: int = 0  # 最大连续盈利次数
    max_consecutive_losses: int = 0  # 最大连续亏损次数


class Strategy(ABC):
    """策略基类"""
    
    @abstractmethod
    def generate_signals(self, data: pd.DataFrame) -> pd.Series:
        """
        生成交易信号
        
        Returns:
            pd.Series: 交易信号 (1=买入, -1=卖出, 0=持有)
        """
        pass
    
    def on_bar(self, bar: pd.Series, portfolio: Dict[str, Any]) -> Optional[str]:
        """
        事件驱动模式的bar处理（可选实现）
        
        Args:
            bar: 当前K线数据
            portfolio: 当前投资组合状态
            
        Returns:
            Optional[str]: 交易信号 ('buy', 'sell', 'hold', None)
        """
        return None


class BacktestEngine:
    """回测引擎"""
    
    def __init__(self, config: Optional[BacktestConfig] = None):
        self.config = config or BacktestConfig()
        self.reset()
    
    def reset(self):
        """重置回测状态"""
        self.capital = self.config.initial_capital
        self.positions: Dict[str, Position] = {}
        self.trades: List[Trade] = []
        self.equity_curve: List[float] = []
        self.timestamps: List[datetime] = []
    
    def run(
        self,
        strategy: Strategy,
        data: pd.DataFrame,
        symbols: Optional[List[str]] = None,
        detail_data: Optional[pd.DataFrame] = None,
    ) -> BacktestResult:
        """
        运行回测
        
        Args:
            strategy: 交易策略
            data: 历史数据（必须包含OHLCV列）
            symbols: 交易标的列表
            detail_data: 【整改#2】可选的更细周期数据（如 1m），索引为 datetime。
                         仅事件驱动模式使用；若提供且 timeframe_detail 已配置，则
                         逐根子 K 线扫描以精确判定 SL/TP 谁先被触及（对标 Freqtrade
                         --timeframe-detail）。向量化模式忽略该参数。
            
        Returns:
            BacktestResult: 回测结果
        """
        self.reset()
        
        if self.config.mode == BacktestMode.VECTORIZED:
            return self._run_vectorized(strategy, data, symbols)
        else:
            return self._run_event_driven(strategy, data, symbols, detail_data)
    
    def _run_vectorized(
        self,
        strategy: Strategy,
        data: pd.DataFrame,
        symbols: Optional[List[str]] = None
    ) -> BacktestResult:
        """向量化回测（快速模式）"""
        # 生成交易信号
        signals = strategy.generate_signals(data)
        
        # 计算收益
        returns = data['close'].pct_change()
        strategy_returns = signals.shift(1) * returns
        
        # 扣除交易成本（Phase 3B：使用真实费率）
        slippage_rate = (
            _unified_slippage_rate(
                notional_usd=self.config.initial_capital * 0.1,  # 假设10%仓位
                trade_nature="swing",
            )
            if self.config.use_dynamic_slippage
            else self.config.slippage
        )
        # 交易成本仅在"仓位变化(换手)的 bar"计提：trades_mask=|Δsignal|，
        # 一次满仓反手(如 -1→+1)换手=2，即平旧仓+开新仓两份成本，符合向量化标准约定。
        # 修复：此前引用了从未定义的 trades_mask 变量，而 VECTORIZED 为默认模式 → 默认回测必崩(NameError)。
        trades_mask = signals.diff().abs().fillna(0)
        transaction_costs = trades_mask * (self.config.commission + slippage_rate)
        strategy_returns = strategy_returns - transaction_costs
        
        # 计算权益曲线
        equity_curve = (1 + strategy_returns).cumprod() * self.config.initial_capital
        
        # 生成交易记录
        trades = self._generate_trades_from_signals(data, signals)
        
        # 计算回测指标
        return self._calculate_metrics(
            data.index[0],
            data.index[-1],
            equity_curve,
            trades
        )
    
    def _run_event_driven(
        self,
        strategy: Strategy,
        data: pd.DataFrame,
        symbols: Optional[List[str]] = None,
        detail_data: Optional[pd.DataFrame] = None,
    ) -> BacktestResult:
        """事件驱动回测（精确模式）"""
        # 【整改#2】预处理子 K 线映射 {主bar时间戳: 该 bar 内的细粒度子 K 线 DataFrame}
        detail_map = self._build_detail_map(data, detail_data)
        # 【整改#2】next_open 成交模型：信号延到下一根开盘价成交（防前视）
        pending_signal: Optional[str] = None

        for timestamp, bar in data.iterrows():
            # 更新持仓价格
            self._update_positions(bar)

            # 先执行上一根挂起的订单：在"本根开盘价"成交（next_open 模型，防前视）
            if pending_signal is not None:
                fill_price = self._resolve_fill_price(bar)
                self._execute_signal(pending_signal, bar, timestamp, fill_price)
                pending_signal = None

            # 检查止损止盈（传入本 bar 对应的子 K 线，若有）
            self._check_stop_loss_take_profit(bar, timestamp, detail_map.get(timestamp))
            
            # 获取交易信号
            portfolio = self._get_portfolio_state()
            signal = strategy.on_bar(bar, portfolio)
            
            # 执行交易
            if signal in ('buy', 'sell'):
                if self.config.fill_model == 'next_open':
                    # 延到下一根开盘成交
                    pending_signal = signal
                else:
                    # close 模型：立即在本根收盘价成交（等价旧行为）
                    self._execute_signal(signal, bar, timestamp, None)
            
            # 记录权益
            equity = self._calculate_equity(bar)
            self.equity_curve.append(equity)
            self.timestamps.append(timestamp)
        
        # 生成回测结果
        equity_series = pd.Series(self.equity_curve, index=self.timestamps)
        return self._calculate_metrics(
            data.index[0],
            data.index[-1],
            equity_series,
            self.trades
        )
    
    def _update_positions(self, bar: pd.Series):
        """更新持仓当前价格"""
        for symbol, position in self.positions.items():
            if 'close' in bar:
                position.current_price = bar['close']
                position.unrealized_pnl = (
                    (position.current_price - position.entry_price) 
                    * position.quantity
                )
    
    def _check_stop_loss_take_profit(
        self,
        bar: pd.Series,
        timestamp: datetime,
        detail_bars: Optional[pd.DataFrame] = None,
    ):
        """检查止损止盈（【整改#2】支持三种解析模式）

        1. close（默认）：仅用收盘价的浮盈浮亏判定 —— 完全等价于改造前行为。
        2. intrabar high/low（intrabar_resolution=True 且无 detail 数据）：
           用本 bar 的 high/low 判断是否穿透 SL/TP；两者都触及时保守假设"止损先"。
        3. detail 解析（提供 detail_bars）：在子 K 线序列中逐根扫描，精确判定谁先触及。

        触发时的成交价：close 模式沿用收盘价（保持旧行为）；intrabar/detail 模式
        以 SL/TP 触发价成交（更贴近真实）。
        """
        if not (self.config.stop_loss_pct or self.config.take_profit_pct):
            return

        for symbol in list(self.positions.keys()):
            position = self.positions[symbol]
            reason, exit_price = self._decide_exit(position, bar, detail_bars)
            if reason == 'stop_loss':
                self._close_position(symbol, bar, timestamp, "止损", exit_price=exit_price)
            elif reason == 'take_profit':
                self._close_position(symbol, bar, timestamp, "止盈", exit_price=exit_price)

    def _decide_exit(self, position: 'Position', bar: pd.Series, detail_bars: Optional[pd.DataFrame]):
        """返回 (reason, exit_price)。reason ∈ {None,'stop_loss','take_profit'}。
        close 模式下 exit_price=None（由 _close_position 回退到收盘价）。"""
        sl_pct = self.config.stop_loss_pct
        tp_pct = self.config.take_profit_pct
        # 模式 3：有子 K 线 → 逐根精确扫描
        if detail_bars is not None and len(detail_bars) > 0:
            reason = self._check_intrabar_detailed(position, sl_pct, tp_pct, detail_bars)
            return reason, self._trigger_price(position, reason)
        # 模式 2：high/low 穿透（需要 bar 含 high/low）
        if self.config.intrabar_resolution and ('high' in bar) and ('low' in bar):
            reason = self._check_intrabar_highlow(position, sl_pct, tp_pct, bar)
            return reason, self._trigger_price(position, reason)
        # 模式 1：收盘价（旧行为）
        reason = self._check_close_price(position, sl_pct, tp_pct)
        return reason, None

    def _check_close_price(self, position: 'Position', sl_pct, tp_pct) -> Optional[str]:
        """收盘价浮盈浮亏判定（改造前逻辑，逐字保留）。"""
        pnl_pct = position.unrealized_pnl / (position.entry_price * abs(position.quantity))
        if sl_pct and pnl_pct <= -sl_pct:
            return 'stop_loss'
        if tp_pct and pnl_pct >= tp_pct:
            return 'take_profit'
        return None

    def _check_intrabar_highlow(self, position: 'Position', sl_pct, tp_pct, bar: pd.Series) -> Optional[str]:
        """无 detail 数据时的保守 high/low 判定（多空自适应）。
        两者都被触及 → 保守假设止损先触发（防乐观偏差）。"""
        is_long = position.quantity >= 0
        hi = float(bar['high'])
        lo = float(bar['low'])
        entry = position.entry_price
        if is_long:
            sl_price = entry * (1 - sl_pct) if sl_pct else None
            tp_price = entry * (1 + tp_pct) if tp_pct else None
            hit_sl = sl_price is not None and lo <= sl_price
            hit_tp = tp_price is not None and hi >= tp_price
        else:
            sl_price = entry * (1 + sl_pct) if sl_pct else None
            tp_price = entry * (1 - tp_pct) if tp_pct else None
            hit_sl = sl_price is not None and hi >= sl_price
            hit_tp = tp_price is not None and lo <= tp_price
        if hit_sl:
            return 'stop_loss'  # 保守：SL 先
        if hit_tp:
            return 'take_profit'
        return None

    def _check_intrabar_detailed(self, position: 'Position', sl_pct, tp_pct, detail_bars: pd.DataFrame) -> Optional[str]:
        """对标 Freqtrade timeframe-detail：逐根子 K 线扫描，返回最先触及者。"""
        for _, sub_bar in detail_bars.iterrows():
            reason = self._check_intrabar_highlow(position, sl_pct, tp_pct, sub_bar)
            if reason:
                return reason
        return None

    def _trigger_price(self, position: 'Position', reason: Optional[str]) -> Optional[float]:
        """intrabar/detail 模式下的成交价 = SL/TP 触发价（多空自适应）。"""
        if reason is None:
            return None
        is_long = position.quantity >= 0
        entry = position.entry_price
        if reason == 'stop_loss' and self.config.stop_loss_pct:
            return entry * (1 - self.config.stop_loss_pct) if is_long else entry * (1 + self.config.stop_loss_pct)
        if reason == 'take_profit' and self.config.take_profit_pct:
            return entry * (1 + self.config.take_profit_pct) if is_long else entry * (1 - self.config.take_profit_pct)
        return None

    def _build_detail_map(self, data: pd.DataFrame, detail_data: Optional[pd.DataFrame]) -> Dict[Any, pd.DataFrame]:
        """【整改#2】把细粒度数据切分到每根主 K 线区间 [T, T_next)。"""
        detail_map: Dict[Any, pd.DataFrame] = {}
        if detail_data is None or len(detail_data) == 0:
            return detail_map
        try:
            index_list = list(data.index)
            for i, ts in enumerate(index_list):
                nxt = index_list[i + 1] if (i + 1) < len(index_list) else None
                if nxt is not None:
                    sub = detail_data.loc[(detail_data.index >= ts) & (detail_data.index < nxt)]
                else:
                    sub = detail_data.loc[detail_data.index >= ts]
                if len(sub) > 0:
                    detail_map[ts] = sub
        except Exception:
            # 索引不可比较/对齐失败时安全降级：不使用 detail 解析
            return {}
        return detail_map

    def _resolve_fill_price(self, execution_bar: pd.Series) -> float:
        """【整改#2】成交价模型：next_open→本根开盘价；否则收盘价。"""
        if self.config.fill_model == 'next_open' and 'open' in execution_bar:
            return float(execution_bar['open'])
        return float(execution_bar['close'])

    def _execute_signal(self, signal: str, bar: pd.Series, timestamp: datetime, fill_price: Optional[float]):
        """统一的信号执行分发（buy/sell），支持显式成交价（next_open 模型用）。"""
        if signal == 'buy':
            self._execute_buy(bar, timestamp, fill_price=fill_price)
        elif signal == 'sell':
            self._execute_sell(bar, timestamp, fill_price=fill_price)
    
    def _execute_buy(self, bar: pd.Series, timestamp: datetime, fill_price: Optional[float] = None):
        """执行买入（fill_price 为 None 时回退到收盘价，等价旧行为）"""
        symbol = bar.get('symbol', 'DEFAULT')
        price = float(fill_price) if fill_price is not None else bar['close']
        
        # 计算可买入数量
        available_capital = self.capital * self.config.max_position_size
        quantity = available_capital / price
        
        if quantity <= 0:
            return
        
        # 计算交易成本
        commission = quantity * price * self.config.commission
        slippage = quantity * price * self.config.slippage
        total_cost = quantity * price + commission + slippage
        
        if total_cost > self.capital:
            return
        
        # 更新资金和持仓
        self.capital -= total_cost
        
        if symbol in self.positions:
            position = self.positions[symbol]
            position.quantity += quantity
            position.entry_price = (
                (position.entry_price * (position.quantity - quantity) + price * quantity)
                / position.quantity
            )
        else:
            self.positions[symbol] = Position(
                symbol=symbol,
                quantity=quantity,
                entry_price=price,
                current_price=price
            )
        
        # 记录交易
        self.trades.append(Trade(
            timestamp=timestamp,
            symbol=symbol,
            side='buy',
            price=price,
            quantity=quantity,
            commission=commission,
            slippage=slippage
        ))
    
    def _execute_sell(self, bar: pd.Series, timestamp: datetime, fill_price: Optional[float] = None):
        """执行卖出（fill_price 透传给平仓，用于 next_open 成交模型）"""
        symbol = bar.get('symbol', 'DEFAULT')
        
        if symbol not in self.positions:
            return
        
        self._close_position(symbol, bar, timestamp, "信号卖出", exit_price=fill_price)
    
    def _close_position(
        self,
        symbol: str,
        bar: pd.Series,
        timestamp: datetime,
        reason: str,
        exit_price: Optional[float] = None,
    ):
        """平仓（exit_price 为 None 时回退到收盘价，等价旧行为）"""
        position = self.positions[symbol]
        price = float(exit_price) if exit_price is not None else bar['close']
        quantity = position.quantity
        
        # 计算交易成本
        commission = quantity * price * self.config.commission
        slippage = quantity * price * self.config.slippage
        
        # 计算盈亏
        pnl = (price - position.entry_price) * quantity - commission - slippage
        
        # 更新资金
        self.capital += quantity * price - commission - slippage
        
        # 记录交易
        self.trades.append(Trade(
            timestamp=timestamp,
            symbol=symbol,
            side='sell',
            price=price,
            quantity=quantity,
            commission=commission,
            slippage=slippage,
            pnl=pnl
        ))
        
        # 移除持仓
        del self.positions[symbol]
    
    def _get_portfolio_state(self) -> Dict[str, Any]:
        """获取投资组合状态"""
        return {
            'capital': self.capital,
            'positions': self.positions.copy(),
            'total_value': self.capital + sum(
                pos.quantity * pos.current_price for pos in self.positions.values()
            )
        }
    
    def _calculate_equity(self, bar: pd.Series) -> float:
        """计算当前权益"""
        positions_value = sum(
            pos.quantity * pos.current_price for pos in self.positions.values()
        )
        return self.capital + positions_value
    
    def _generate_trades_from_signals(
        self,
        data: pd.DataFrame,
        signals: pd.Series
    ) -> List[Trade]:
        """从信号生成交易记录"""
        trades = []
        signal_changes = signals.diff()
        
        for timestamp, change in signal_changes.items():
            if abs(change) > 0:
                price = data.loc[timestamp, 'close']
                side = 'buy' if change > 0 else 'sell'
                
                trades.append(Trade(
                    timestamp=timestamp,
                    symbol='DEFAULT',
                    side=side,
                    price=price,
                    quantity=abs(change),
                    commission=abs(change) * price * self.config.commission,
                    slippage=abs(change) * price * self.config.slippage
                ))
        
        return trades
    
    def _calculate_metrics(
        self,
        start_date: datetime,
        end_date: datetime,
        equity_curve: pd.Series,
        trades: List[Trade]
    ) -> BacktestResult:
        """计算回测指标"""
        # 基本指标
        initial_capital = self.config.initial_capital
        final_capital = equity_curve.iloc[-1]
        total_return = (final_capital - initial_capital) / initial_capital
        
        # 计算年化收益
        days = (end_date - start_date).days
        years = days / 365.25
        annualized_return = (1 + total_return) ** (1 / years) - 1 if years > 0 else 0
        
        # 计算收益率序列
        returns = equity_curve.pct_change().dropna()
        
        # 计算最大回撤
        cummax = equity_curve.cummax()
        drawdown = (equity_curve - cummax) / cummax
        max_drawdown = abs(drawdown.min())
        
        # 计算夏普比率
        if returns.std() > 0:
            sharpe_ratio = np.sqrt(252) * returns.mean() / returns.std()
        else:
            sharpe_ratio = 0.0
        
        # 计算索提诺比率
        negative_returns = returns[returns < 0]
        if len(negative_returns) > 0 and negative_returns.std() > 0:
            sortino_ratio = np.sqrt(252) * returns.mean() / negative_returns.std()
        else:
            sortino_ratio = 0.0
        
        # 计算卡玛比率
        calmar_ratio = annualized_return / max_drawdown if max_drawdown > 0 else 0.0
        
        # 交易统计
        total_trades = len(trades)
        profitable_trades = [t for t in trades if t.pnl and t.pnl > 0]
        losing_trades_list = [t for t in trades if t.pnl and t.pnl < 0]
        
        winning_trades = len(profitable_trades)
        losing_trades = len(losing_trades_list)
        win_rate = winning_trades / total_trades if total_trades > 0 else 0.0
        
        avg_win = np.mean([t.pnl for t in profitable_trades]) if profitable_trades else 0.0
        avg_loss = np.mean([t.pnl for t in losing_trades_list]) if losing_trades_list else 0.0
        profit_factor = abs(avg_win / avg_loss) if avg_loss != 0 else 0.0
        
        # 计算最大连续盈亏
        consecutive_wins = 0
        consecutive_losses = 0
        max_consecutive_wins = 0
        max_consecutive_losses = 0
        
        for trade in trades:
            if trade.pnl and trade.pnl > 0:
                consecutive_wins += 1
                consecutive_losses = 0
                max_consecutive_wins = max(max_consecutive_wins, consecutive_wins)
            elif trade.pnl and trade.pnl < 0:
                consecutive_losses += 1
                consecutive_wins = 0
                max_consecutive_losses = max(max_consecutive_losses, consecutive_losses)
        
        return BacktestResult(
            start_date=start_date,
            end_date=end_date,
            initial_capital=initial_capital,
            final_capital=final_capital,
            total_return=total_return,
            annualized_return=annualized_return,
            max_drawdown=max_drawdown,
            sharpe_ratio=sharpe_ratio,
            sortino_ratio=sortino_ratio,
            calmar_ratio=calmar_ratio,
            total_trades=total_trades,
            winning_trades=winning_trades,
            losing_trades=losing_trades,
            win_rate=win_rate,
            equity_curve=equity_curve,
            trades=trades,
            positions=list(self.positions.values()),
            avg_win=avg_win,
            avg_loss=avg_loss,
            profit_factor=profit_factor,
            max_consecutive_wins=max_consecutive_wins,
            max_consecutive_losses=max_consecutive_losses
        )
