"""
ATAS V2 图表生成器

生成回测可视化图表
"""
from enum import Enum
from typing import Optional, List
import pandas as pd
import numpy as np


class ChartType(Enum):
    """图表类型"""
    EQUITY_CURVE = "equity_curve"
    DRAWDOWN = "drawdown"
    RETURNS_DISTRIBUTION = "returns_distribution"
    MONTHLY_RETURNS = "monthly_returns"
    TRADE_ANALYSIS = "trade_analysis"


class BacktestChartGenerator:
    """回测图表生成器"""
    
    def __init__(self):
        pass
    
    def generate(
        self,
        backtest_result: any,
        chart_types: List[ChartType],
        output_path: Optional[str] = None
    ):
        """
        生成图表
        
        Args:
            backtest_result: 回测结果
            chart_types: 图表类型列表
            output_path: 输出路径
        """
        try:
            import matplotlib.pyplot as plt
            import matplotlib.dates as mdates
            
            num_charts = len(chart_types)
            fig, axes = plt.subplots(num_charts, 1, figsize=(12, 5*num_charts))
            
            if num_charts == 1:
                axes = [axes]
            
            for i, chart_type in enumerate(chart_types):
                if chart_type == ChartType.EQUITY_CURVE:
                    self._plot_equity_curve(axes[i], backtest_result)
                elif chart_type == ChartType.DRAWDOWN:
                    self._plot_drawdown(axes[i], backtest_result)
                elif chart_type == ChartType.RETURNS_DISTRIBUTION:
                    self._plot_returns_distribution(axes[i], backtest_result)
                elif chart_type == ChartType.MONTHLY_RETURNS:
                    self._plot_monthly_returns(axes[i], backtest_result)
                elif chart_type == ChartType.TRADE_ANALYSIS:
                    self._plot_trade_analysis(axes[i], backtest_result)
            
            plt.tight_layout()
            
            if output_path:
                plt.savefig(output_path, dpi=300, bbox_inches='tight')
            else:
                plt.show()
            
            plt.close()
        
        except ImportError:
            raise ImportError("matplotlib is required for chart generation")
    
    def _plot_equity_curve(self, ax, result):
        """绘制权益曲线"""
        equity = result.equity_curve
        ax.plot(equity.index, equity.values, linewidth=2, color='#2E86AB')
        ax.fill_between(equity.index, equity.values, alpha=0.3, color='#2E86AB')
        ax.set_title('权益曲线', fontsize=14, fontweight='bold')
        ax.set_xlabel('日期')
        ax.set_ylabel('权益 ($)')
        ax.grid(True, alpha=0.3)
        ax.yaxis.set_major_formatter(plt.FuncFormatter(lambda x, p: f'${x:,.0f}'))
    
    def _plot_drawdown(self, ax, result):
        """绘制回撤图"""
        equity = result.equity_curve
        cummax = equity.cummax()
        drawdown = (equity - cummax) / cummax * 100
        
        ax.fill_between(drawdown.index, drawdown.values, 0, 
                        where=drawdown.values < 0, color='red', alpha=0.3)
        ax.plot(drawdown.index, drawdown.values, color='red', linewidth=1.5)
        ax.set_title('回撤曲线', fontsize=14, fontweight='bold')
        ax.set_xlabel('日期')
        ax.set_ylabel('回撤 (%)')
        ax.grid(True, alpha=0.3)
        ax.axhline(y=0, color='black', linestyle='-', linewidth=0.5)
    
    def _plot_returns_distribution(self, ax, result):
        """绘制收益分布"""
        returns = result.equity_curve.pct_change().dropna() * 100
        
        ax.hist(returns, bins=50, color='#06D6A0', alpha=0.7, edgecolor='black')
        ax.axvline(returns.mean(), color='red', linestyle='--', 
                  linewidth=2, label=f'均值: {returns.mean():.2f}%')
        ax.axvline(0, color='black', linestyle='-', linewidth=1)
        ax.set_title('收益率分布', fontsize=14, fontweight='bold')
        ax.set_xlabel('收益率 (%)')
        ax.set_ylabel('频数')
        ax.legend()
        ax.grid(True, alpha=0.3, axis='y')
    
    def _plot_monthly_returns(self, ax, result):
        """绘制月度收益热力图"""
        equity = result.equity_curve
        monthly_returns = equity.resample('M').last().pct_change() * 100
        
        years = monthly_returns.index.year.unique()
        months = range(1, 13)
        
        data = np.full((len(years), 12), np.nan)
        
        for idx, (date, ret) in enumerate(monthly_returns.items()):
            year_idx = np.where(years == date.year)[0][0]
            month_idx = date.month - 1
            data[year_idx, month_idx] = ret
        
        im = ax.imshow(data, cmap='RdYlGn', aspect='auto', vmin=-10, vmax=10)
        ax.set_xticks(range(12))
        ax.set_xticklabels(['1月', '2月', '3月', '4月', '5月', '6月',
                           '7月', '8月', '9月', '10月', '11月', '12月'])
        ax.set_yticks(range(len(years)))
        ax.set_yticklabels(years)
        ax.set_title('月度收益热力图 (%)', fontsize=14, fontweight='bold')
        
        plt.colorbar(im, ax=ax)
    
    def _plot_trade_analysis(self, ax, result):
        """绘制交易分析"""
        if not result.trades or len(result.trades) == 0:
            ax.text(0.5, 0.5, '无交易数据', ha='center', va='center')
            ax.set_title('交易分析', fontsize=14, fontweight='bold')
            return
        
        trades_with_pnl = [t for t in result.trades if hasattr(t, 'pnl') and t.pnl]
        
        if not trades_with_pnl:
            ax.text(0.5, 0.5, '无盈亏数据', ha='center', va='center')
            ax.set_title('交易分析', fontsize=14, fontweight='bold')
            return
        
        cumulative_pnl = np.cumsum([t.pnl for t in trades_with_pnl])
        colors = ['green' if t.pnl > 0 else 'red' for t in trades_with_pnl]
        
        ax.plot(range(len(cumulative_pnl)), cumulative_pnl, 
               color='blue', linewidth=2, label='累计盈亏')
        ax.bar(range(len(trades_with_pnl)), [t.pnl for t in trades_with_pnl],
              color=colors, alpha=0.5, label='单笔盈亏')
        
        ax.set_title('交易盈亏分析', fontsize=14, fontweight='bold')
        ax.set_xlabel('交易序号')
        ax.set_ylabel('盈亏 ($)')
        ax.legend()
        ax.grid(True, alpha=0.3)
        ax.axhline(y=0, color='black', linestyle='-', linewidth=0.5)


def generate_charts(
    backtest_result: any,
    chart_types: List[ChartType] = None,
    output_path: Optional[str] = None
):
    """便捷函数：生成图表"""
    if chart_types is None:
        chart_types = [ChartType.EQUITY_CURVE, ChartType.DRAWDOWN]
    
    generator = BacktestChartGenerator()
    generator.generate(backtest_result, chart_types, output_path)
