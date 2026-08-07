"""
ATAS V2 信号质量分析器

分析交易信号的质量、准确率、盈亏比等
"""
import logging
import numpy as np
import pandas as pd
from typing import Dict, Any, List, Optional, Tuple
from dataclasses import dataclass, asdict
from datetime import datetime

logger = logging.getLogger(__name__)


@dataclass
class SignalQualityMetrics:
    """信号质量指标"""
    # 准确率指标
    total_signals: int
    correct_signals: int
    accuracy: float  # 准确率
    precision: float  # 精确率
    recall: float  # 召回率
    f1_score: float
    
    # 盈亏指标
    avg_profit_per_signal: float
    avg_loss_per_signal: float
    profit_loss_ratio: float  # 盈亏比
    win_rate: float
    
    # 信号分布
    long_signals: int
    short_signals: int
    signal_balance: float  # 多空平衡度
    
    # 时间特征
    avg_signal_duration: float  # 平均信号持续时间
    signal_frequency: float  # 信号频率（每天）
    
    # 稳定性指标
    signal_consistency: float  # 信号一致性
    false_positive_rate: float
    false_negative_rate: float
    
    def to_dict(self) -> dict:
        return asdict(self)


class SignalAnalyzer:
    """
    信号质量分析器
    
    分析交易信号的质量和性能
    """
    
    def __init__(self):
        logger.info("SignalAnalyzer initialized")
    
    def analyze(
        self,
        signals: pd.Series,
        prices: pd.Series,
        actual_returns: Optional[pd.Series] = None,
        holding_period: int = 1
    ) -> SignalQualityMetrics:
        """
        分析信号质量
        
        Args:
            signals: 信号序列（1=做多, -1=做空, 0=无信号）
            prices: 价格序列
            actual_returns: 实际收益序列（可选）
            holding_period: 持仓周期
            
        Returns:
            信号质量指标
        """
        if len(signals) != len(prices):
            raise ValueError("Signals and prices must have the same length")
        
        # 计算未来收益（用于评估信号准确性）
        if actual_returns is None:
            actual_returns = prices.pct_change(holding_period).shift(-holding_period)
        
        # 基本统计
        total_signals = (signals != 0).sum()
        long_signals = (signals > 0).sum()
        short_signals = (signals < 0).sum()
        
        # 准确率计算
        correct_long = ((signals > 0) & (actual_returns > 0)).sum()
        correct_short = ((signals < 0) & (actual_returns < 0)).sum()
        correct_signals = correct_long + correct_short
        
        accuracy = correct_signals / total_signals if total_signals > 0 else 0
        
        # 混淆矩阵指标
        true_positive = correct_long
        false_positive = ((signals > 0) & (actual_returns <= 0)).sum()
        true_negative = correct_short
        false_negative = ((signals < 0) & (actual_returns >= 0)).sum()
        
        precision = true_positive / (true_positive + false_positive) if (true_positive + false_positive) > 0 else 0
        recall = true_positive / (true_positive + false_negative) if (true_positive + false_negative) > 0 else 0
        f1_score = 2 * (precision * recall) / (precision + recall) if (precision + recall) > 0 else 0
        
        # 盈亏分析
        signal_returns = signals.shift(1) * actual_returns
        profitable_signals = signal_returns[signal_returns > 0]
        losing_signals = signal_returns[signal_returns < 0]
        
        avg_profit = profitable_signals.mean() if len(profitable_signals) > 0 else 0
        avg_loss = losing_signals.mean() if len(losing_signals) > 0 else 0
        profit_loss_ratio = abs(avg_profit / avg_loss) if avg_loss != 0 else 0
        win_rate = len(profitable_signals) / total_signals if total_signals > 0 else 0
        
        # 信号分布
        signal_balance = min(long_signals, short_signals) / max(long_signals, short_signals) if max(long_signals, short_signals) > 0 else 0
        
        # 时间特征
        avg_duration = self._calculate_avg_signal_duration(signals)
        signal_frequency = total_signals / len(signals) * 252  # 年化频率
        
        # 稳定性指标
        signal_consistency = self._calculate_consistency(signals, actual_returns)
        false_positive_rate = false_positive / (false_positive + true_negative) if (false_positive + true_negative) > 0 else 0
        false_negative_rate = false_negative / (false_negative + true_positive) if (false_negative + true_positive) > 0 else 0
        
        return SignalQualityMetrics(
            total_signals=int(total_signals),
            correct_signals=int(correct_signals),
            accuracy=accuracy,
            precision=precision,
            recall=recall,
            f1_score=f1_score,
            avg_profit_per_signal=avg_profit,
            avg_loss_per_signal=avg_loss,
            profit_loss_ratio=profit_loss_ratio,
            win_rate=win_rate,
            long_signals=int(long_signals),
            short_signals=int(short_signals),
            signal_balance=signal_balance,
            avg_signal_duration=avg_duration,
            signal_frequency=signal_frequency,
            signal_consistency=signal_consistency,
            false_positive_rate=false_positive_rate,
            false_negative_rate=false_negative_rate
        )
    
    def analyze_by_time(
        self,
        signals: pd.Series,
        prices: pd.Series,
        time_periods: List[str] = ['morning', 'afternoon', 'evening']
    ) -> Dict[str, SignalQualityMetrics]:
        """
        按时间段分析信号质量
        
        Args:
            signals: 信号序列
            prices: 价格序列
            time_periods: 时间段列表
            
        Returns:
            各时间段的信号质量指标
        """
        results = {}
        
        if not isinstance(signals.index, pd.DatetimeIndex):
            logger.warning("Signals index is not DatetimeIndex, skipping time-based analysis")
            return results
        
        # 定义时间段
        time_ranges = {
            'morning': (0, 12),
            'afternoon': (12, 18),
            'evening': (18, 24)
        }
        
        for period in time_periods:
            if period not in time_ranges:
                continue
            
            start_hour, end_hour = time_ranges[period]
            mask = (signals.index.hour >= start_hour) & (signals.index.hour < end_hour)
            
            period_signals = signals[mask]
            period_prices = prices[mask]
            
            if len(period_signals) > 0:
                results[period] = self.analyze(period_signals, period_prices)
        
        return results
    
    def analyze_by_market_condition(
        self,
        signals: pd.Series,
        prices: pd.Series,
        volatility: pd.Series
    ) -> Dict[str, SignalQualityMetrics]:
        """
        按市场条件分析信号质量
        
        Args:
            signals: 信号序列
            prices: 价格序列
            volatility: 波动率序列
            
        Returns:
            不同市场条件下的信号质量
        """
        results = {}
        
        # 定义市场条件
        vol_median = volatility.median()
        
        conditions = {
            'low_volatility': volatility < vol_median,
            'high_volatility': volatility >= vol_median
        }
        
        for condition_name, mask in conditions.items():
            condition_signals = signals[mask]
            condition_prices = prices[mask]
            
            if len(condition_signals) > 0:
                results[condition_name] = self.analyze(condition_signals, condition_prices)
        
        return results
    
    def _calculate_avg_signal_duration(self, signals: pd.Series) -> float:
        """计算平均信号持续时间"""
        signal_changes = signals.diff().abs()
        signal_starts = signal_changes > 0
        
        durations = []
        current_duration = 0
        
        for i, is_start in enumerate(signal_starts):
            if is_start and i > 0:
                if current_duration > 0:
                    durations.append(current_duration)
                current_duration = 1
            elif signals.iloc[i] != 0:
                current_duration += 1
        
        if current_duration > 0:
            durations.append(current_duration)
        
        return np.mean(durations) if durations else 0
    
    def _calculate_consistency(self, signals: pd.Series, actual_returns: pd.Series) -> float:
        """
        计算信号一致性
        
        衡量信号方向与实际收益方向的一致程度
        """
        signal_return_alignment = signals.shift(1) * actual_returns
        positive_alignment = (signal_return_alignment > 0).sum()
        total_signals = (signals != 0).sum()
        
        return positive_alignment / total_signals if total_signals > 0 else 0
    
    def compare_strategies(
        self,
        strategies: Dict[str, Tuple[pd.Series, pd.Series]]
    ) -> pd.DataFrame:
        """
        比较多个策略的信号质量
        
        Args:
            strategies: 策略字典 {strategy_name: (signals, prices)}
            
        Returns:
            比较结果DataFrame
        """
        results = []
        
        for strategy_name, (signals, prices) in strategies.items():
            metrics = self.analyze(signals, prices)
            result = {'strategy': strategy_name, **metrics.to_dict()}
            results.append(result)
        
        return pd.DataFrame(results)
    
    def generate_signal_report(
        self,
        signals: pd.Series,
        prices: pd.Series,
        strategy_name: str = "Strategy"
    ) -> str:
        """
        生成信号质量报告
        
        Args:
            signals: 信号序列
            prices: 价格序列
            strategy_name: 策略名称
            
        Returns:
            报告文本
        """
        metrics = self.analyze(signals, prices)
        
        report = f"""
=== {strategy_name} 信号质量报告 ===

信号统计:
- 总信号数: {metrics.total_signals}
- 做多信号: {metrics.long_signals}
- 做空信号: {metrics.short_signals}
- 信号频率: {metrics.signal_frequency:.2f} 次/年

准确率指标:
- 准确率: {metrics.accuracy:.2%}
- 精确率: {metrics.precision:.2%}
- 召回率: {metrics.recall:.2%}
- F1分数: {metrics.f1_score:.3f}

盈亏指标:
- 胜率: {metrics.win_rate:.2%}
- 盈亏比: {metrics.profit_loss_ratio:.2f}
- 平均盈利: {metrics.avg_profit_per_signal:.4f}
- 平均亏损: {metrics.avg_loss_per_signal:.4f}

稳定性:
- 信号一致性: {metrics.signal_consistency:.2%}
- 假阳性率: {metrics.false_positive_rate:.2%}
- 假阴性率: {metrics.false_negative_rate:.2%}

时间特征:
- 平均持续时间: {metrics.avg_signal_duration:.1f} 周期
- 多空平衡度: {metrics.signal_balance:.2f}
"""
        
        return report


# 便捷函数
def analyze_signal_performance(
    signals: pd.Series,
    prices: pd.Series,
    holding_period: int = 1
) -> SignalQualityMetrics:
    """快速分析信号性能"""
    analyzer = SignalAnalyzer()
    return analyzer.analyze(signals, prices, holding_period=holding_period)
