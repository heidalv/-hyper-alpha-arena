"""
ATAS V2 策略对比分析

多策略横向对比功能
"""
from dataclasses import dataclass
from typing import List, Dict, Any
import pandas as pd
import numpy as np


@dataclass
class ComparisonResult:
    """对比结果"""
    summary: pd.DataFrame  # 汇总表
    rankings: Dict[str, List[str]]  # 各指标排名
    correlation_matrix: pd.DataFrame  # 收益相关性矩阵
    best_strategy: str  # 综合最优策略
    diversification_score: float  # 分散化得分


class StrategyComparator:
    """策略对比分析器"""
    
    def __init__(self):
        pass
    
    def compare(
        self,
        results: Dict[str, Any],
        weights: Dict[str, float] = None
    ) -> ComparisonResult:
        """
        对比多个策略
        
        Args:
            results: 策略名称->回测结果映射
            weights: 指标权重
            
        Returns:
            ComparisonResult: 对比结果
        """
        if not results:
            raise ValueError("No results to compare")
        
        # 默认权重
        if weights is None:
            weights = {
                'total_return': 0.25,
                'sharpe_ratio': 0.25,
                'max_drawdown': 0.25,
                'win_rate': 0.25
            }
        
        # 构建汇总表
        summary = self._build_summary(results)
        
        # 计算排名
        rankings = self._calculate_rankings(summary)
        
        # 计算相关性
        correlation_matrix = self._calculate_correlation(results)
        
        # 综合评分
        best_strategy = self._find_best_strategy(summary, weights)
        
        # 分散化得分
        diversification_score = self._calculate_diversification(correlation_matrix)
        
        return ComparisonResult(
            summary=summary,
            rankings=rankings,
            correlation_matrix=correlation_matrix,
            best_strategy=best_strategy,
            diversification_score=diversification_score
        )
    
    def _build_summary(self, results: Dict[str, Any]) -> pd.DataFrame:
        """构建汇总表"""
        data = []
        
        for name, result in results.items():
            data.append({
                '策略名称': name,
                '总收益率': result.total_return,
                '年化收益': result.annualized_return,
                '最大回撤': result.max_drawdown,
                '夏普比率': result.sharpe_ratio,
                '索提诺比率': result.sortino_ratio,
                '卡玛比率': result.calmar_ratio,
                '总交易次数': result.total_trades,
                '胜率': result.win_rate,
                '盈亏比': result.profit_factor,
            })
        
        return pd.DataFrame(data).set_index('策略名称')
    
    def _calculate_rankings(self, summary: pd.DataFrame) -> Dict[str, List[str]]:
        """计算各指标排名"""
        rankings = {}
        
        # 越大越好的指标
        for col in ['总收益率', '年化收益', '夏普比率', '索提诺比率', '卡玛比率', '胜率', '盈亏比']:
            if col in summary.columns:
                rankings[col] = summary[col].sort_values(ascending=False).index.tolist()
        
        # 越小越好的指标
        for col in ['最大回撤']:
            if col in summary.columns:
                rankings[col] = summary[col].sort_values(ascending=True).index.tolist()
        
        return rankings
    
    def _calculate_correlation(self, results: Dict[str, Any]) -> pd.DataFrame:
        """计算收益相关性矩阵"""
        returns_data = {}
        
        for name, result in results.items():
            returns_data[name] = result.equity_curve.pct_change().dropna()
        
        returns_df = pd.DataFrame(returns_data)
        return returns_df.corr()
    
    def _find_best_strategy(
        self,
        summary: pd.DataFrame,
        weights: Dict[str, float]
    ) -> str:
        """根据权重找出最优策略"""
        scores = pd.Series(0.0, index=summary.index)
        
        # 标准化并加权
        for metric, weight in weights.items():
            if metric == 'total_return':
                col = '总收益率'
            elif metric == 'sharpe_ratio':
                col = '夏普比率'
            elif metric == 'max_drawdown':
                col = '最大回撤'
            elif metric == 'win_rate':
                col = '胜率'
            else:
                continue
            
            if col not in summary.columns:
                continue
            
            # 标准化到0-1
            values = summary[col]
            if col == '最大回撤':
                # 回撤越小越好
                normalized = 1 - (values - values.min()) / (values.max() - values.min() + 1e-10)
            else:
                # 其他指标越大越好
                normalized = (values - values.min()) / (values.max() - values.min() + 1e-10)
            
            scores += normalized * weight
        
        return scores.idxmax()
    
    def _calculate_diversification(self, correlation_matrix: pd.DataFrame) -> float:
        """计算分散化得分"""
        # 平均相关系数（排除对角线）
        mask = ~np.eye(correlation_matrix.shape[0], dtype=bool)
        avg_correlation = correlation_matrix.values[mask].mean()
        
        # 转换为分散化得分（相关性越低，分散化越好）
        diversification_score = 1 - abs(avg_correlation)
        
        return diversification_score
    
    def plot_comparison(
        self,
        results: Dict[str, Any],
        output_path: str = None
    ):
        """绘制对比图表"""
        try:
            import matplotlib.pyplot as plt
            
            fig, axes = plt.subplots(2, 2, figsize=(15, 12))
            
            # 1. 权益曲线对比
            ax = axes[0, 0]
            for name, result in results.items():
                equity = result.equity_curve
                normalized = equity / equity.iloc[0] * 100
                ax.plot(normalized.index, normalized.values, label=name, linewidth=2)
            ax.set_title('权益曲线对比（归一化）', fontsize=14)
            ax.set_xlabel('日期')
            ax.set_ylabel('权益 (起始=100)')
            ax.legend()
            ax.grid(True, alpha=0.3)
            
            # 2. 收益对比
            ax = axes[0, 1]
            names = list(results.keys())
            returns = [results[name].total_return * 100 for name in names]
            colors = ['green' if r > 0 else 'red' for r in returns]
            ax.bar(names, returns, color=colors, alpha=0.7)
            ax.set_title('总收益率对比', fontsize=14)
            ax.set_ylabel('收益率 (%)')
            ax.axhline(y=0, color='black', linestyle='-', linewidth=0.5)
            ax.grid(True, alpha=0.3, axis='y')
            
            # 3. 风险指标对比
            ax = axes[1, 0]
            sharpe = [results[name].sharpe_ratio for name in names]
            sortino = [results[name].sortino_ratio for name in names]
            x = np.arange(len(names))
            width = 0.35
            ax.bar(x - width/2, sharpe, width, label='Sharpe', alpha=0.7)
            ax.bar(x + width/2, sortino, width, label='Sortino', alpha=0.7)
            ax.set_title('风险调整收益对比', fontsize=14)
            ax.set_xticks(x)
            ax.set_xticklabels(names)
            ax.set_ylabel('比率')
            ax.legend()
            ax.grid(True, alpha=0.3, axis='y')
            
            # 4. 最大回撤对比
            ax = axes[1, 1]
            drawdowns = [results[name].max_drawdown * 100 for name in names]
            ax.bar(names, drawdowns, color='red', alpha=0.7)
            ax.set_title('最大回撤对比', fontsize=14)
            ax.set_ylabel('最大回撤 (%)')
            ax.grid(True, alpha=0.3, axis='y')
            
            plt.tight_layout()
            
            if output_path:
                plt.savefig(output_path, dpi=300, bbox_inches='tight')
            else:
                plt.show()
            
            plt.close()
        
        except ImportError:
            raise ImportError("matplotlib is required for plotting")


def compare_strategies(
    results: Dict[str, Any],
    weights: Dict[str, float] = None
) -> ComparisonResult:
    """便捷函数：对比策略"""
    comparator = StrategyComparator()
    return comparator.compare(results, weights)
