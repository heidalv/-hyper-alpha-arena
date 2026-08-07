"""
Performance Metrics - 性能指标计算

提供全面的交易性能指标计算：
1. 收益指标
2. 风险指标
3. 效率指标
4. 比较分析

Author: Hyper-Alpha-Arena
"""

import logging
from typing import Dict, List, Optional, Tuple, Any
from dataclasses import dataclass, field
from enum import Enum
from datetime import datetime, timedelta, timezone
import numpy as np
import pandas as pd

logger = logging.getLogger(__name__)


class MetricType(Enum):
    """指标类型"""
    RETURN = "return"  # 收益指标
    RISK = "risk"  # 风险指标
    EFFICIENCY = "efficiency"  # 效率指标
    COMPARISON = "comparison"  # 比较指标


@dataclass
class PerformanceMetrics:
    """性能指标"""
    # 基本信息
    period_start: datetime
    period_end: datetime
    total_trades: int
    winning_trades: int
    losing_trades: int
    
    # 收益指标
    total_pnl: float
    total_pnl_pct: float
    avg_win: float
    avg_loss: float
    avg_trade_pnl: float
    best_trade_pct: float
    worst_trade_pct: float
    
    # 风险指标
    max_drawdown: float
    max_drawdown_pct: float
    current_drawdown: float
    volatility: float
    sharpe_ratio: float
    sortino_ratio: float
    calmar_ratio: float
    var_95: float
    
    # 效率指标
    win_rate: float
    profit_factor: float
    expectancy: float
    recovery_factor: float
    risk_reward_ratio: float
    expectancy_ratio: float
    
    # 时间指标
    avg_holding_period: float
    longest_holding_period: float
    trades_per_day: float
    
    # 序列指标
    consecutive_wins: int
    consecutive_losses: int
    avg_time_to_first_profit: float
    
    # 额外数据
    final_equity: float
    initial_equity: float
    max_equity: float
    min_equity: float
    by_symbol: Dict[str, Dict] = field(default_factory=dict)


class PerformanceAnalyzer:
    """
    性能分析器
    
    计算和跟踪各种交易性能指标
    """
    
    def __init__(self):
        self.trade_history: List[Dict] = []
        self.equity_curve: List[Dict] = []
        self.analysis_cache: Dict[str, PerformanceMetrics] = {}
    
    def add_trade(self, trade: Dict):
        """添加交易记录"""
        self.trade_history.append(trade)
        logger.debug(f"[PerformanceAnalyzer] Added trade: {trade.get('symbol')} {trade.get('pnl_pct'):.2f}%")
    
    def add_equity_point(self, equity: float, timestamp: datetime):
        """添加权益点位"""
        self.equity_curve.append({
            'equity': equity,
            'timestamp': timestamp
        })
    
    def analyze_period(
        self,
        trades: Optional[List[Dict]] = None,
        equity_curve: Optional[List[Dict]] = None,
        initial_equity: float = 10000.0,
        risk_free_rate: float = 0.02
    ) -> PerformanceMetrics:
        """
        分析指定期间的绩效
        
        Args:
            trades: 交易记录列表
            equity_curve: 权益曲线
            initial_equity: 初始资金
            risk_free_rate: 无风险利率 (年化)
            
        Returns:
            PerformanceMetrics对象
        """
        analysis_trades = trades or self.trade_history
        analysis_equity = equity_curve or self.equity_curve
        
        if not analysis_trades:
            return self._empty_metrics()
        
        analysis_trades.sort(key=lambda x: x.get('exit_time', datetime.min))
        
        period_start = analysis_trades[0].get('exit_time', datetime.now(timezone.utc))
        period_end = analysis_trades[-1].get('exit_time', datetime.now(timezone.utc))
        
        pnl_values = [t.get('pnl', 0) for t in analysis_trades]
        pnl_pcts = [t.get('pnl_pct', 0) for t in analysis_trades]
        
        winning_trades = [t for t in analysis_trades if t.get('pnl', 0) > 0]
        losing_trades = [t for t in analysis_trades if t.get('pnl', 0) <= 0]
        
        wins = [t.get('pnl_pct', 0) for t in winning_trades]
        losses = [abs(t.get('pnl_pct', 0)) for t in losing_trades if t.get('pnl_pct', 0) < 0]
        
        equity_series = self._build_equity_series(analysis_equity, analysis_trades, initial_equity)
        
        metrics = PerformanceMetrics(
            period_start=period_start,
            period_end=period_end,
            total_trades=len(analysis_trades),
            winning_trades=len(winning_trades),
            losing_trades=len(losing_trades),
            total_pnl=sum(pnl_values),
            total_pnl_pct=sum(pnl_pcts),
            avg_win=np.mean(wins) if wins else 0,
            avg_loss=np.mean(losses) if losses else 0,
            avg_trade_pnl=np.mean(pnl_values) if pnl_values else 0,
            best_trade_pct=max(pnl_pcts) if pnl_pcts else 0,
            worst_trade_pct=min(pnl_pcts) if pnl_pcts else 0,
            max_drawdown=self._calculate_max_drawdown(equity_series),
            max_drawdown_pct=self._calculate_max_drawdown_pct(equity_series),
            current_drawdown=self._calculate_current_drawdown(equity_series),
            volatility=self._calculate_volatility(pnl_pcts),
            sharpe_ratio=self._calculate_sharpe_ratio(pnl_pcts, risk_free_rate),
            sortino_ratio=self._calculate_sortino_ratio(pnl_pcts, risk_free_rate),
            calmar_ratio=self._calculate_calmar_ratio(pnl_pcts, equity_series),
            var_95=self._calculate_var(pnl_pcts),
            win_rate=len(winning_trades) / len(analysis_trades) if analysis_trades else 0,
            profit_factor=sum(wins) / sum(losses) if losses and sum(losses) > 0 else 0,
            expectancy=self._calculate_expectancy(wins, losses),
            recovery_factor=self._calculate_recovery_factor(sum(pnl_values), self._calculate_max_drawdown(equity_series)),
            risk_reward_ratio=np.mean(wins) / np.mean(losses) if wins and losses and np.mean(losses) > 0 else 0,
            expectancy_ratio=self._calculate_expectancy_ratio(wins, losses),
            avg_holding_period=self._calculate_avg_holding_period(analysis_trades),
            longest_holding_period=self._calculate_longest_holding_period(analysis_trades),
            trades_per_day=self._calculate_trades_per_day(analysis_trades, period_start, period_end),
            consecutive_wins=self._calculate_consecutive(analysis_trades, 'win'),
            consecutive_losses=self._calculate_consecutive(analysis_trades, 'loss'),
            avg_time_to_first_profit=self._calculate_avg_time_to_first_profit(analysis_trades),
            final_equity=equity_series[-1]['equity'] if equity_series else initial_equity,
            initial_equity=initial_equity,
            max_equity=max(e['equity'] for e in equity_series) if equity_series else initial_equity,
            min_equity=min(e['equity'] for e in equity_series) if equity_series else initial_equity,
            by_symbol=self._analyze_by_symbol(analysis_trades)
        )
        
        return metrics
    
    def _build_equity_series(
        self,
        equity_points: List[Dict],
        trades: List[Dict],
        initial_equity: float
    ) -> List[Dict]:
        """构建权益序列"""
        equity_series = []
        current_equity = initial_equity
        
        if equity_points:
            for point in equity_points:
                equity_series.append({
                    'equity': point['equity'],
                    'timestamp': point.get('timestamp', datetime.now(timezone.utc))
                })
            current_equity = equity_series[-1]['equity']
        else:
            equity_series.append({
                'equity': initial_equity,
                'timestamp': datetime.now(timezone.utc)
            })
        
        for trade in trades:
            current_equity += trade.get('pnl', 0)
            equity_series.append({
                'equity': current_equity,
                'timestamp': trade.get('exit_time', datetime.now(timezone.utc))
            })
        
        return equity_series
    
    def _calculate_max_drawdown(self, equity_series: List[Dict]) -> float:
        """计算最大回撤金额"""
        if not equity_series:
            return 0.0
        
        max_equity = equity_series[0]['equity']
        max_drawdown = 0.0
        
        for point in equity_series:
            if point['equity'] > max_equity:
                max_equity = point['equity']
            drawdown = max_equity - point['equity']
            if drawdown > max_drawdown:
                max_drawdown = drawdown
        
        return max_drawdown
    
    def _calculate_max_drawdown_pct(self, equity_series: List[Dict]) -> float:
        """计算最大回撤百分比"""
        if not equity_series:
            return 0.0
        
        max_equity = equity_series[0]['equity']
        max_dd_pct = 0.0
        
        for point in equity_series:
            if point['equity'] > max_equity:
                max_equity = point['equity']
            if max_equity > 0:
                dd_pct = (max_equity - point['equity']) / max_equity * 100
                if dd_pct > max_dd_pct:
                    max_dd_pct = dd_pct
        
        return max_dd_pct
    
    def _calculate_current_drawdown(self, equity_series: List[Dict]) -> float:
        """计算当前回撤"""
        if not equity_series:
            return 0.0
        
        current_equity = equity_series[-1]['equity']
        max_equity = max(e['equity'] for e in equity_series)
        
        if max_equity == 0:
            return 0.0
        
        return (max_equity - current_equity) / max_equity * 100
    
    def _calculate_volatility(self, pnl_pcts: List[float]) -> float:
        """计算波动率 (年化)"""
        if not pnl_pcts:
            return 0.0
        
        return np.std(pnl_pcts) * np.sqrt(365) if len(pnl_pcts) > 1 else 0.0
    
    def _calculate_sharpe_ratio(
        self,
        pnl_pcts: List[float],
        risk_free_rate: float
    ) -> float:
        """计算夏普比率"""
        if len(pnl_pcts) < 2:
            return 0.0
        
        mean_return = np.mean(pnl_pcts)
        std_return = np.std(pnl_pcts, ddof=1)
        
        if std_return == 0:
            return 0.0
        
        return (mean_return - risk_free_rate / 365) / std_return * np.sqrt(252)
    
    def _calculate_sortino_ratio(
        self,
        pnl_pcts: List[float],
        risk_free_rate: float
    ) -> float:
        """计算索提诺比率"""
        if len(pnl_pcts) < 2:
            return 0.0
        
        mean_return = np.mean(pnl_pcts)
        downside_returns = [r for r in pnl_pcts if r < 0]
        downside_std = np.std(downside_returns, ddof=1) if downside_returns else 0.01
        
        if downside_std == 0:
            return 0.0
        
        return (mean_return - risk_free_rate / 365) / downside_std * np.sqrt(252)
    
    def _calculate_calmar_ratio(
        self,
        pnl_pcts: List[float],
        equity_series: List[Dict]
    ) -> float:
        """计算卡玛比率"""
        max_dd = self._calculate_max_drawdown_pct(equity_series)
        total_return = (equity_series[-1]['equity'] - equity_series[0]['equity']) / equity_series[0]['equity'] * 100 if equity_series else 0
        
        years = len(equity_series) / 365 if equity_series else 1
        annual_return = total_return / years if years > 0 else 0
        
        if max_dd == 0:
            return 0.0
        
        return annual_return / max_dd
    
    def _calculate_var(self, pnl_pcts: List[float], confidence: float = 0.95) -> float:
        """计算风险价值 (VaR)"""
        if not pnl_pcts:
            return 0.0
        
        return np.percentile(pnl_pcts, (1 - confidence) * 100)
    
    def _calculate_expectancy(self, wins: List[float], losses: List[float]) -> float:
        """计算期望收益"""
        if not wins and not losses:
            return 0.0
        
        avg_win = np.mean(wins) if wins else 0
        avg_loss = np.mean(losses) if losses else 0
        win_rate = len(wins) / (len(wins) + len(losses)) if (len(wins) + len(losses)) > 0 else 0
        
        return win_rate * avg_win - (1 - win_rate) * avg_loss
    
    def _calculate_recovery_factor(self, total_pnl: float, max_drawdown: float) -> float:
        """计算恢复因子"""
        if max_drawdown == 0:
            return 0.0
        
        return total_pnl / max_drawdown
    
    def _calculate_expectancy_ratio(self, wins: List[float], losses: List[float]) -> float:
        """计算期望比率"""
        expectancy = self._calculate_expectancy(wins, losses)
        avg_trade = np.mean(wins + losses) if (wins + losses) else 1
        
        if avg_trade == 0:
            return 0.0
        
        return expectancy / abs(avg_trade)
    
    def _calculate_avg_holding_period(self, trades: List[Dict]) -> float:
        """计算平均持仓时间 (小时)"""
        if not trades:
            return 0.0
        
        holding_periods = []
        for trade in trades:
            entry_time = trade.get('entry_time')
            exit_time = trade.get('exit_time')
            if entry_time and exit_time:
                if isinstance(entry_time, str):
                    entry_time = datetime.fromisoformat(entry_time.replace('Z', '+00:00'))
                if isinstance(exit_time, str):
                    exit_time = datetime.fromisoformat(exit_time.replace('Z', '+00:00'))
                holding_periods.append((exit_time - entry_time).total_seconds() / 3600)
        
        return np.mean(holding_periods) if holding_periods else 0
    
    def _calculate_longest_holding_period(self, trades: List[Dict]) -> float:
        """计算最长持仓时间 (小时)"""
        if not trades:
            return 0.0
        
        max_period = 0
        for trade in trades:
            entry_time = trade.get('entry_time')
            exit_time = trade.get('exit_time')
            if entry_time and exit_time:
                if isinstance(entry_time, str):
                    entry_time = datetime.fromisoformat(entry_time.replace('Z', '+00:00'))
                if isinstance(exit_time, str):
                    exit_time = datetime.fromisoformat(exit_time.replace('Z', '+00:00'))
                period = (exit_time - entry_time).total_seconds() / 3600
                if period > max_period:
                    max_period = period
        
        return max_period
    
    def _calculate_trades_per_day(
        self,
        trades: List[Dict],
        start: datetime,
        end: datetime
    ) -> float:
        """计算日均交易次数"""
        if not trades or end <= start:
            return 0.0
        
        days = (end - start).days
        return len(trades) / max(days, 1)
    
    def _calculate_consecutive(self, trades: List[Dict], trade_type: str) -> int:
        """计算最大连续盈利/亏损次数"""
        if not trades:
            return 0
        
        max_consecutive = 0
        current_consecutive = 0
        
        for trade in trades:
            is_win = trade.get('pnl', 0) > 0
            if (trade_type == 'win' and is_win) or (trade_type == 'loss' and not is_win):
                current_consecutive += 1
                max_consecutive = max(max_consecutive, current_consecutive)
            else:
                current_consecutive = 0
        
        return max_consecutive
    
    def _calculate_avg_time_to_first_profit(self, trades: List[Dict]) -> float:
        """计算平均首次盈利时间 (小时)"""
        times = []
        for trade in trades:
            entry_time = trade.get('entry_time')
            if entry_time and isinstance(entry_time, str):
                entry_time = datetime.fromisoformat(entry_time.replace('Z', '+00:00'))
            times.append(0)  # 简化实现
        
        return np.mean(times) if times else 0
    
    def _analyze_by_symbol(self, trades: List[Dict]) -> Dict:
        """按品种分析"""
        by_symbol = {}
        
        for trade in trades:
            symbol = trade.get('symbol', 'UNKNOWN')
            if symbol not in by_symbol:
                by_symbol[symbol] = {
                    'trades': 0,
                    'wins': 0,
                    'pnl': 0,
                    'pnl_pct': 0,
                    'win_rate': 0
                }
            
            by_symbol[symbol]['trades'] += 1
            if trade.get('pnl', 0) > 0:
                by_symbol[symbol]['wins'] += 1
            by_symbol[symbol]['pnl'] += trade.get('pnl', 0)
            by_symbol[symbol]['pnl_pct'] += trade.get('pnl_pct', 0)
        
        for symbol in by_symbol:
            data = by_symbol[symbol]
            data['win_rate'] = data['wins'] / data['trades'] if data['trades'] > 0 else 0
        
        return by_symbol
    
    def _empty_metrics(self) -> PerformanceMetrics:
        """返回空指标"""
        return PerformanceMetrics(
            period_start=datetime.now(timezone.utc),
            period_end=datetime.now(timezone.utc),
            total_trades=0,
            winning_trades=0,
            losing_trades=0,
            total_pnl=0,
            total_pnl_pct=0,
            avg_win=0,
            avg_loss=0,
            avg_trade_pnl=0,
            best_trade_pct=0,
            worst_trade_pct=0,
            max_drawdown=0,
            max_drawdown_pct=0,
            current_drawdown=0,
            volatility=0,
            sharpe_ratio=0,
            sortino_ratio=0,
            calmar_ratio=0,
            var_95=0,
            win_rate=0,
            profit_factor=0,
            expectancy=0,
            recovery_factor=0,
            risk_reward_ratio=0,
            expectancy_ratio=0,
            avg_holding_period=0,
            longest_holding_period=0,
            trades_per_day=0,
            consecutive_wins=0,
            consecutive_losses=0,
            avg_time_to_first_profit=0,
            final_equity=0,
            initial_equity=0,
            max_equity=0,
            min_equity=0
        )
    
    def compare_periods(
        self,
        periods: List[Tuple[str, List[Dict]]],
        initial_equity: float = 10000.0
    ) -> Dict:
        """比较多个期间的绩效"""
        results = {}
        
        for name, trades in periods:
            metrics = self.analyze_period(trades, initial_equity=initial_equity)
            results[name] = {
                'total_pnl': metrics.total_pnl,
                'total_pnl_pct': metrics.total_pnl_pct,
                'win_rate': metrics.win_rate,
                'sharpe_ratio': metrics.sharpe_ratio,
                'max_drawdown_pct': metrics.max_drawdown_pct,
                'trades': metrics.total_trades,
                'profit_factor': metrics.profit_factor
            }
        
        return results
    
    def get_performance_summary(self) -> Dict:
        """获取性能汇总"""
        if not self.trade_history:
            return {'status': 'no_data'}
        
        metrics = self.analyze_period()
        
        return {
            'status': 'analyzed',
            'period': {
                'start': metrics.period_start.isoformat(),
                'end': metrics.period_end.isoformat()
            },
            'returns': {
                'total_pnl': metrics.total_pnl,
                'total_pnl_pct': metrics.total_pnl_pct,
                'avg_trade_pnl': metrics.avg_trade_pnl,
                'best_trade': metrics.best_trade_pct,
                'worst_trade': metrics.worst_trade_pct
            },
            'risk': {
                'max_drawdown_pct': metrics.max_drawdown_pct,
                'current_drawdown': metrics.current_drawdown,
                'volatility': metrics.volatility,
                'sharpe_ratio': metrics.sharpe_ratio,
                'sortino_ratio': metrics.sortino_ratio,
                'var_95': metrics.var_95
            },
            'efficiency': {
                'win_rate': metrics.win_rate,
                'profit_factor': metrics.profit_factor,
                'expectancy': metrics.expectancy,
                'avg_holding_hours': metrics.avg_holding_period
            },
            'consistency': {
                'consecutive_wins': metrics.consecutive_wins,
                'consecutive_losses': metrics.consecutive_losses,
                'trades_per_day': metrics.trades_per_day
            }
        }


# 全局实例
_performance_analyzer: Optional[PerformanceAnalyzer] = None


def get_performance_analyzer() -> PerformanceAnalyzer:
    """获取全局性能分析器"""
    global _performance_analyzer
    if _performance_analyzer is None:
        _performance_analyzer = PerformanceAnalyzer()
    return _performance_analyzer


def calculate_all_metrics(
    trades: List[Dict],
    initial_equity: float = 10000.0
) -> PerformanceMetrics:
    """便捷函数：计算所有指标"""
    analyzer = get_performance_analyzer()
    return analyzer.analyze_period(trades, initial_equity=initial_equity)
