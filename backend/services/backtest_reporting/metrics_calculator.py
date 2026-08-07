"""
ATAS V2 回测指标计算器

提供全面的回测性能指标计算
"""
from dataclasses import dataclass
from typing import List, Optional
import pandas as pd
import numpy as np


@dataclass
class PerformanceMetrics:
    """性能指标"""
    # 收益指标
    total_return: float = 0.0
    annualized_return: float = 0.0
    cumulative_return: float = 0.0
    
    # 风险指标
    volatility: float = 0.0
    annualized_volatility: float = 0.0
    max_drawdown: float = 0.0
    max_drawdown_duration: int = 0
    
    # 风险调整收益
    sharpe_ratio: float = 0.0
    sortino_ratio: float = 0.0
    calmar_ratio: float = 0.0
    omega_ratio: float = 0.0
    
    # 交易统计
    total_trades: int = 0
    winning_trades: int = 0
    losing_trades: int = 0
    win_rate: float = 0.0
    avg_win: float = 0.0
    avg_loss: float = 0.0
    profit_factor: float = 0.0
    
    # VaR风险
    var_95: float = 0.0
    var_99: float = 0.0
    cvar_95: float = 0.0
    cvar_99: float = 0.0
    
    # 其他指标
    recovery_factor: float = 0.0
    ulcer_index: float = 0.0


class BacktestMetricsCalculator:
    """回测指标计算器"""
    
    def __init__(self, risk_free_rate: float = 0.02):
        self.risk_free_rate = risk_free_rate
    
    def calculate(
        self,
        equity_curve: pd.Series,
        trades: Optional[List] = None
    ) -> PerformanceMetrics:
        """
        计算全部性能指标
        
        Args:
            equity_curve: 权益曲线
            trades: 交易记录列表
            
        Returns:
            PerformanceMetrics: 性能指标
        """
        returns = equity_curve.pct_change().dropna()
        
        metrics = PerformanceMetrics()
        
        # 收益指标
        metrics.total_return = self._calculate_total_return(equity_curve)
        metrics.annualized_return = self._calculate_annualized_return(equity_curve)
        metrics.cumulative_return = metrics.total_return
        
        # 风险指标
        metrics.volatility = returns.std()
        metrics.annualized_volatility = metrics.volatility * np.sqrt(252)
        metrics.max_drawdown, metrics.max_drawdown_duration = self._calculate_max_drawdown(equity_curve)
        
        # 风险调整收益
        metrics.sharpe_ratio = self._calculate_sharpe_ratio(returns)
        metrics.sortino_ratio = self._calculate_sortino_ratio(returns)
        metrics.calmar_ratio = metrics.annualized_return / metrics.max_drawdown if metrics.max_drawdown > 0 else 0
        metrics.omega_ratio = self._calculate_omega_ratio(returns)
        
        # VaR
        metrics.var_95 = self._calculate_var(returns, 0.95)
        metrics.var_99 = self._calculate_var(returns, 0.99)
        metrics.cvar_95 = self._calculate_cvar(returns, 0.95)
        metrics.cvar_99 = self._calculate_cvar(returns, 0.99)
        
        # 其他指标
        metrics.recovery_factor = metrics.total_return / metrics.max_drawdown if metrics.max_drawdown > 0 else 0
        metrics.ulcer_index = self._calculate_ulcer_index(equity_curve)
        
        # 交易统计
        if trades:
            metrics.total_trades = len(trades)
            profitable = [t for t in trades if hasattr(t, 'pnl') and t.pnl and t.pnl > 0]
            losing = [t for t in trades if hasattr(t, 'pnl') and t.pnl and t.pnl < 0]
            
            metrics.winning_trades = len(profitable)
            metrics.losing_trades = len(losing)
            metrics.win_rate = metrics.winning_trades / metrics.total_trades if metrics.total_trades > 0 else 0
            
            metrics.avg_win = np.mean([t.pnl for t in profitable]) if profitable else 0
            metrics.avg_loss = np.mean([t.pnl for t in losing]) if losing else 0
            metrics.profit_factor = abs(metrics.avg_win / metrics.avg_loss) if metrics.avg_loss != 0 else 0
        
        return metrics
    
    def _calculate_total_return(self, equity_curve: pd.Series) -> float:
        """计算总收益率"""
        return (equity_curve.iloc[-1] - equity_curve.iloc[0]) / equity_curve.iloc[0]
    
    def _calculate_annualized_return(self, equity_curve: pd.Series) -> float:
        """计算年化收益率"""
        total_return = self._calculate_total_return(equity_curve)
        days = (equity_curve.index[-1] - equity_curve.index[0]).days
        years = days / 365.25
        return (1 + total_return) ** (1 / years) - 1 if years > 0 else 0
    
    def _calculate_max_drawdown(self, equity_curve: pd.Series) -> tuple:
        """计算最大回撤和持续期"""
        cummax = equity_curve.cummax()
        drawdown = (equity_curve - cummax) / cummax
        max_dd = abs(drawdown.min())
        
        # 计算最大回撤持续期
        in_drawdown = drawdown < 0
        drawdown_periods = []
        current_period = 0
        
        for is_dd in in_drawdown:
            if is_dd:
                current_period += 1
            else:
                if current_period > 0:
                    drawdown_periods.append(current_period)
                current_period = 0
        
        if current_period > 0:
            drawdown_periods.append(current_period)
        
        max_dd_duration = max(drawdown_periods) if drawdown_periods else 0
        
        return max_dd, max_dd_duration
    
    def _calculate_sharpe_ratio(self, returns: pd.Series) -> float:
        """计算夏普比率"""
        if returns.std() == 0:
            return 0.0
        excess_returns = returns - self.risk_free_rate / 252
        return np.sqrt(252) * excess_returns.mean() / returns.std()
    
    def _calculate_sortino_ratio(self, returns: pd.Series) -> float:
        """计算索提诺比率"""
        negative_returns = returns[returns < 0]
        if len(negative_returns) == 0 or negative_returns.std() == 0:
            return 0.0
        excess_returns = returns - self.risk_free_rate / 252
        return np.sqrt(252) * excess_returns.mean() / negative_returns.std()
    
    def _calculate_omega_ratio(self, returns: pd.Series, threshold: float = 0.0) -> float:
        """计算Omega比率"""
        gains = returns[returns > threshold].sum()
        losses = abs(returns[returns < threshold].sum())
        return gains / losses if losses > 0 else 0.0
    
    def _calculate_var(self, returns: pd.Series, confidence_level: float) -> float:
        """计算VaR"""
        return np.percentile(returns, (1 - confidence_level) * 100)
    
    def _calculate_cvar(self, returns: pd.Series, confidence_level: float) -> float:
        """计算CVaR"""
        var = self._calculate_var(returns, confidence_level)
        return returns[returns <= var].mean()
    
    def _calculate_ulcer_index(self, equity_curve: pd.Series) -> float:
        """计算溃疡指数"""
        cummax = equity_curve.cummax()
        drawdown = (equity_curve - cummax) / cummax
        return np.sqrt(np.mean(drawdown ** 2))


def calculate_performance_metrics(
    equity_curve: pd.Series,
    trades: Optional[List] = None,
    risk_free_rate: float = 0.02
) -> PerformanceMetrics:
    """便捷函数：计算性能指标"""
    calculator = BacktestMetricsCalculator(risk_free_rate)
    return calculator.calculate(equity_curve, trades)
