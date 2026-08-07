"""
ATAS V2 风险评估器

提供夏普率、最大回撤、VaR等风险指标计算
"""
import logging
import numpy as np
import pandas as pd
from typing import Dict, Any, List, Optional, Tuple
from dataclasses import dataclass, asdict
from datetime import datetime

logger = logging.getLogger(__name__)


@dataclass
class RiskMetrics:
    """风险指标"""
    # 收益指标
    total_return: float
    annualized_return: float
    cumulative_return: float
    
    # 风险指标
    volatility: float
    annualized_volatility: float
    max_drawdown: float
    max_drawdown_duration: int
    
    # 风险调整收益
    sharpe_ratio: float
    sortino_ratio: float
    calmar_ratio: float
    
    # VaR和CVaR
    var_95: float
    var_99: float
    cvar_95: float
    cvar_99: float
    
    # 交易统计
    total_trades: int
    win_rate: float
    profit_factor: float
    avg_win: float
    avg_loss: float
    max_consecutive_wins: int
    max_consecutive_losses: int
    
    # 其他
    recovery_factor: float  # 总收益/最大回撤
    ulcer_index: float  # 衡量下行波动
    
    def to_dict(self) -> dict:
        return asdict(self)


class RiskEvaluator:
    """
    风险评估器
    
    计算策略的各种风险指标
    """
    
    def __init__(self, risk_free_rate: float = 0.0):
        """
        初始化风险评估器
        
        Args:
            risk_free_rate: 无风险利率（年化）
        """
        self.risk_free_rate = risk_free_rate
        logger.info(f"RiskEvaluator initialized with risk_free_rate={risk_free_rate}")
    
    def evaluate(
        self,
        returns: pd.Series,
        trades: Optional[List[Dict[str, Any]]] = None
    ) -> RiskMetrics:
        """
        评估风险指标
        
        Args:
            returns: 收益率序列（日收益率）
            trades: 交易记录列表（可选）
            
        Returns:
            风险指标对象
        """
        if len(returns) == 0:
            raise ValueError("Returns series is empty")
        
        # 收益指标
        total_return = self._calculate_total_return(returns)
        annualized_return = self._annualize_return(returns)
        cumulative_return = (1 + returns).cumprod()[-1] - 1
        
        # 风险指标
        volatility = returns.std()
        annualized_volatility = volatility * np.sqrt(252)
        max_dd, max_dd_duration = self._calculate_max_drawdown(returns)
        
        # 风险调整收益
        sharpe = self._calculate_sharpe_ratio(returns)
        sortino = self._calculate_sortino_ratio(returns)
        calmar = annualized_return / max_dd if max_dd != 0 else 0
        
        # VaR和CVaR
        var_95 = self._calculate_var(returns, 0.95)
        var_99 = self._calculate_var(returns, 0.99)
        cvar_95 = self._calculate_cvar(returns, 0.95)
        cvar_99 = self._calculate_cvar(returns, 0.99)
        
        # 交易统计
        if trades:
            trade_stats = self._analyze_trades(trades)
        else:
            trade_stats = {
                'total_trades': 0,
                'win_rate': 0.0,
                'profit_factor': 0.0,
                'avg_win': 0.0,
                'avg_loss': 0.0,
                'max_consecutive_wins': 0,
                'max_consecutive_losses': 0
            }
        
        # 其他指标
        recovery_factor = total_return / abs(max_dd) if max_dd != 0 else 0
        ulcer_index = self._calculate_ulcer_index(returns)
        
        return RiskMetrics(
            total_return=total_return,
            annualized_return=annualized_return,
            cumulative_return=cumulative_return,
            volatility=volatility,
            annualized_volatility=annualized_volatility,
            max_drawdown=max_dd,
            max_drawdown_duration=max_dd_duration,
            sharpe_ratio=sharpe,
            sortino_ratio=sortino,
            calmar_ratio=calmar,
            var_95=var_95,
            var_99=var_99,
            cvar_95=cvar_95,
            cvar_99=cvar_99,
            recovery_factor=recovery_factor,
            ulcer_index=ulcer_index,
            **trade_stats
        )
    
    def _calculate_total_return(self, returns: pd.Series) -> float:
        """计算总收益率"""
        return (1 + returns).prod() - 1
    
    def _annualize_return(self, returns: pd.Series) -> float:
        """年化收益率"""
        total_return = self._calculate_total_return(returns)
        n_periods = len(returns)
        n_years = n_periods / 252  # 假设252个交易日
        
        if n_years > 0:
            return (1 + total_return) ** (1 / n_years) - 1
        return 0.0
    
    def _calculate_max_drawdown(self, returns: pd.Series) -> Tuple[float, int]:
        """
        计算最大回撤和最大回撤持续期
        
        Returns:
            (最大回撤比例, 最大回撤持续天数)
        """
        cumulative = (1 + returns).cumprod()
        running_max = cumulative.expanding().max()
        drawdown = (cumulative - running_max) / running_max
        
        max_dd = drawdown.min()
        
        # 计算回撤持续期
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
        
        return abs(max_dd), max_dd_duration
    
    def _calculate_sharpe_ratio(self, returns: pd.Series) -> float:
        """计算夏普比率"""
        if returns.std() == 0:
            return 0.0
        
        excess_returns = returns - self.risk_free_rate / 252
        return np.sqrt(252) * excess_returns.mean() / returns.std()
    
    def _calculate_sortino_ratio(self, returns: pd.Series) -> float:
        """计算索提诺比率（只考虑下行波动）"""
        excess_returns = returns - self.risk_free_rate / 252
        downside_returns = returns[returns < 0]
        
        if len(downside_returns) == 0 or downside_returns.std() == 0:
            return 0.0
        
        return np.sqrt(252) * excess_returns.mean() / downside_returns.std()
    
    def _calculate_var(self, returns: pd.Series, confidence_level: float) -> float:
        """
        计算VaR（Value at Risk）
        
        Args:
            returns: 收益率序列
            confidence_level: 置信水平（如0.95表示95%）
            
        Returns:
            VaR值（负数表示损失）
        """
        return np.percentile(returns, (1 - confidence_level) * 100)
    
    def _calculate_cvar(self, returns: pd.Series, confidence_level: float) -> float:
        """
        计算CVaR（Conditional Value at Risk，条件风险价值）
        
        Args:
            returns: 收益率序列
            confidence_level: 置信水平
            
        Returns:
            CVaR值
        """
        var = self._calculate_var(returns, confidence_level)
        return returns[returns <= var].mean()
    
    def _calculate_ulcer_index(self, returns: pd.Series) -> float:
        """
        计算溃疡指数（Ulcer Index）
        衡量下行波动的深度和持续时间
        """
        cumulative = (1 + returns).cumprod()
        running_max = cumulative.expanding().max()
        drawdown = (cumulative - running_max) / running_max
        
        return np.sqrt((drawdown ** 2).mean())
    
    def _analyze_trades(self, trades: List[Dict[str, Any]]) -> Dict[str, Any]:
        """
        分析交易记录
        
        Args:
            trades: 交易记录，每条包含 {'pnl': float, ...}
            
        Returns:
            交易统计字典
        """
        if not trades:
            return {
                'total_trades': 0,
                'win_rate': 0.0,
                'profit_factor': 0.0,
                'avg_win': 0.0,
                'avg_loss': 0.0,
                'max_consecutive_wins': 0,
                'max_consecutive_losses': 0
            }
        
        pnls = [t.get('pnl', 0) for t in trades]
        
        wins = [p for p in pnls if p > 0]
        losses = [p for p in pnls if p < 0]
        
        win_rate = len(wins) / len(pnls) if pnls else 0
        
        total_profit = sum(wins)
        total_loss = abs(sum(losses))
        profit_factor = total_profit / total_loss if total_loss != 0 else 0
        
        avg_win = np.mean(wins) if wins else 0
        avg_loss = np.mean(losses) if losses else 0
        
        # 连续盈亏
        max_consecutive_wins = 0
        max_consecutive_losses = 0
        current_wins = 0
        current_losses = 0
        
        for pnl in pnls:
            if pnl > 0:
                current_wins += 1
                current_losses = 0
                max_consecutive_wins = max(max_consecutive_wins, current_wins)
            elif pnl < 0:
                current_losses += 1
                current_wins = 0
                max_consecutive_losses = max(max_consecutive_losses, current_losses)
            else:
                current_wins = 0
                current_losses = 0
        
        return {
            'total_trades': len(trades),
            'win_rate': win_rate,
            'profit_factor': profit_factor,
            'avg_win': avg_win,
            'avg_loss': avg_loss,
            'max_consecutive_wins': max_consecutive_wins,
            'max_consecutive_losses': max_consecutive_losses
        }


# 便捷函数
def calculate_sharpe_ratio(returns: pd.Series, risk_free_rate: float = 0.0) -> float:
    """计算夏普率"""
    evaluator = RiskEvaluator(risk_free_rate)
    return evaluator._calculate_sharpe_ratio(returns)


def calculate_max_drawdown(returns: pd.Series) -> Tuple[float, int]:
    """计算最大回撤"""
    evaluator = RiskEvaluator()
    return evaluator._calculate_max_drawdown(returns)


def calculate_var(returns: pd.Series, confidence_level: float = 0.95) -> float:
    """计算VaR"""
    evaluator = RiskEvaluator()
    return evaluator._calculate_var(returns, confidence_level)
