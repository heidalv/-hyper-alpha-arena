"""
Backtest Reporting Module
"""
from backend.services.backtest_reporting.report_generator import BacktestReportGenerator, ReportFormat
from backend.services.backtest_reporting.metrics_calculator import BacktestMetricsCalculator, PerformanceMetrics
from backend.services.backtest_reporting.chart_generator import BacktestChartGenerator, ChartType
from backend.services.backtest_reporting.strategy_comparator import StrategyComparator, ComparisonResult

__all__ = [
    'BacktestReportGenerator',
    'ReportFormat',
    'BacktestMetricsCalculator',
    'PerformanceMetrics',
    'BacktestChartGenerator',
    'ChartType',
    'StrategyComparator',
    'ComparisonResult',
]
