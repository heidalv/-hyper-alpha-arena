"""
Report Generator - 报告生成器

生成交易分析报告：
1. 性能报告
2. 复盘报告
3. 学习洞察报告
4. 综合报告

Author: Hyper-Alpha-Arena
"""

import logging
from typing import Dict, List, Optional, Any
from datetime import datetime, timedelta, timezone
from dataclasses import dataclass
import json
import pandas as pd

logger = logging.getLogger(__name__)


@dataclass
class ReportConfig:
    """报告配置"""
    title: str = "交易分析报告"
    period_days: int = 30
    include_charts: bool = False
    include_details: bool = True
    format: str = "markdown"  # markdown, html, json
    output_path: Optional[str] = None


class ReportGenerator:
    """
    报告生成器
    
    生成格式化的交易分析报告
    """
    
    def __init__(self):
        self.report_history: List[Dict] = []
    
    def generate_performance_report(
        self,
        metrics: Any,
        config: Optional[ReportConfig] = None
    ) -> str:
        """
        生成性能报告
        
        Args:
            metrics: PerformanceMetrics对象
            config: 报告配置
            
        Returns:
            格式化报告字符串
        """
        cfg = config or ReportConfig()
        
        report = []
        report.append(f"# {cfg.title}")
        report.append(f"\n**生成时间**: {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M:%S UTC')}")
        report.append(f"**分析期间**: {metrics.period_start.strftime('%Y-%m-%d')} 至 {metrics.period_end.strftime('%Y-%m-%d')}")
        report.append("")
        
        report.append("## 📊 收益概览")
        report.append("| 指标 | 值 |")
        report.append("|------|-----|")
        report.append(f"| 总交易次数 | {metrics.total_trades} |")
        report.append(f"| 盈利交易 | {metrics.winning_trades} |")
        report.append(f"| 亏损交易 | {metrics.losing_trades} |")
        report.append(f"| 总盈亏 | ${metrics.total_pnl:,.2f} ({metrics.total_pnl_pct:+.2f}%) |")
        report.append(f"| 平均每笔盈亏 | ${metrics.avg_trade_pnl:,.2f} |")
        report.append(f"| 最佳交易 | +{metrics.best_trade_pct:.2f}% |")
        report.append(f"| 最差交易 | {metrics.worst_trade_pct:.2f}% |")
        report.append("")
        
        report.append("## 🎯 效率指标")
        report.append("| 指标 | 值 | 评级 |")
        report.append("|------|-----|------|")
        report.append(f"| 胜率 | {metrics.win_rate:.1%} | {self._rate_metric(metrics.win_rate, 0.5, 0.6, 0.7)} |")
        report.append(f"| 盈亏比 | {metrics.profit_factor:.2f} | {self._rate_metric(metrics.profit_factor, 1.0, 1.5, 2.0)} |")
        report.append(f"| 期望收益 | {metrics.expectancy:.4f} | {'⭐⭐⭐' if metrics.expectancy > 0.5 else '⭐⭐' if metrics.expectancy > 0 else '⭐'} |")
        report.append(f"| 平均持仓时间 | {metrics.avg_holding_period:.1f} 小时 | - |")
        report.append("")
        
        report.append("## ⚠️ 风险指标")
        report.append("| 指标 | 值 |")
        report.append("|------|-----|")
        report.append(f"| 最大回撤 | {metrics.max_drawdown_pct:.2f}% (${metrics.max_drawdown:,.2f}) |")
        report.append(f"| 当前回撤 | {metrics.current_drawdown:.2f}% |")
        report.append(f"| 年化波动率 | {metrics.volatility:.2f}% |")
        report.append(f"| 夏普比率 | {metrics.sharpe_ratio:.2f} |")
        report.append(f"| 索提诺比率 | {metrics.sortino_ratio:.2f} |")
        report.append(f"| 卡玛比率 | {metrics.calmar_ratio:.2f} |")
        report.append(f"| 95% VaR | {metrics.var_95:.2f}% |")
        report.append("")
        
        if metrics.by_symbol:
            report.append("## 📈 分品种表现")
            report.append("| 品种 | 交易数 | 盈亏 | 胜率 |")
            report.append("|------|--------|------|------|")
            for symbol, data in sorted(metrics.by_symbol.items(), key=lambda x: x[1]['pnl'], reverse=True):
                pnl_str = f"${data['pnl']:,.2f}" if data['pnl'] != int(data['pnl']) else f"${int(data['pnl']):,}"
                report.append(f"| {symbol} | {data['trades']} | {pnl_str} ({data['pnl_pct']:.1f}%) | {data['win_rate']:.1%} |")
            report.append("")
        
        report.append("## 🔄 一致性指标")
        report.append(f"| 指标 | 值 |")
        report.append("|------|-----|")
        report.append(f"| 最大连续盈利 | {metrics.consecutive_wins} 次 |")
        report.append(f"| 最大连续亏损 | {metrics.consecutive_losses} 次 |")
        report.append(f"| 日均交易次数 | {metrics.trades_per_day:.1f} |")
        report.append("")
        
        report.append("## 💰 资金变化")
        report.append(f"- 初始资金: ${metrics.initial_equity:,.2f}")
        report.append(f"- 最终资金: ${metrics.final_equity:,.2f}")
        report.append(f"- 最高资金: ${metrics.max_equity:,.2f}")
        report.append(f"- 最低资金: ${metrics.min_equity:,.2f}")
        report.append(f"- 资金增长: {((metrics.final_equity - metrics.initial_equity) / metrics.initial_equity * 100):.2f}%")
        report.append("")
        
        return "\n".join(report)
    
    def generate_review_report(
        self,
        reviews: List[Any],
        config: Optional[ReportConfig] = None
    ) -> str:
        """
        生成复盘报告
        
        Args:
            reviews: TradeReview列表
            config: 报告配置
            
        Returns:
            格式化报告字符串
        """
        cfg = config or ReportConfig()
        
        report = []
        report.append(f"# 交易复盘报告")
        report.append(f"\n**生成时间**: {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M:%S UTC')}")
        report.append(f"**复盘交易数**: {len(reviews)}")
        report.append("")
        
        if not reviews:
            report.append("暂无复盘数据")
            return "\n".join(report)
        
        avg_score = sum(r.overall_score for r in reviews) / len(reviews)
        
        score_distribution = {
            'excellent': sum(1 for r in reviews if r.overall_score >= 8.5),
            'good': sum(1 for r in reviews if 7.0 <= r.overall_score < 8.5),
            'acceptable': sum(1 for r in reviews if 5.0 <= r.overall_score < 7.0),
            'poor': sum(1 for r in reviews if r.overall_score < 5.0)
        }
        
        report.append("## 📊 复盘概览")
        report.append(f"- 平均综合评分: {avg_score:.2f}/10")
        report.append(f"- 优秀交易: {score_distribution['excellent']} ({score_distribution['excellent']/len(reviews)*100:.1f}%)")
        report.append(f"- 良好交易: {score_distribution['good']} ({score_distribution['good']/len(reviews)*100:.1f}%)")
        report.append(f"- 一般交易: {score_distribution['acceptable']} ({score_distribution['acceptable']/len(reviews)*100:.1f}%)")
        report.append(f"- 需改进交易: {score_distribution['poor']} ({score_distribution['poor']/len(reviews)*100:.1f}%)")
        report.append("")
        
        from backend.services.learning_feedback_layer.trade_review import ReviewDimension
        report.append("## 📈 各维度表现")
        dimension_scores = {}
        for dim in ReviewDimension:
            scores = [r.dimensions.get(dim, type('obj', (object,), {'score': 0})()).score for r in reviews]
            dimension_scores[dim.value] = sum(scores) / len(scores) if scores else 0
        
        sorted_dims = sorted(dimension_scores.items(), key=lambda x: x[1], reverse=True)
        for dim_name, avg_score_dim in sorted_dims:
            bar = "█" * int(avg_score_dim) + "░" * (10 - int(avg_score_dim))
            report.append(f"| {dim_name:20s} | {bar} | {avg_score_dim:.2f}/10 |")
        report.append("")
        
        if cfg.include_details:
            report.append("## 📝 重点复盘")
            flagged_reviews = [r for r in reviews if r.status.value in ['flagged', 'pending']]
            excellent_reviews = [r for r in reviews if r.overall_score >= 8.5]
            
            if excellent_reviews:
                report.append("### ⭐ 优秀交易案例")
                for review in excellent_reviews[:3]:
                    report.append(f"\n#### {review.symbol} {review.side.upper()} - {review.pnl_pct:.2f}%")
                    report.append(f"评分: {review.overall_score:.2f}/10")
                    report.append(f"结论: {review.conclusion}")
                    for dim, score in review.dimensions.items():
                        if score.issues:
                            report.append(f"- {dim.value}: 发现问题 - {'; '.join(score.issues[:2])}")
            
            if flagged_reviews:
                report.append("\n### ⚠️ 需要改进的交易")
                for review in flagged_reviews[:3]:
                    report.append(f"\n#### {review.symbol} {review.side.upper()} - {review.pnl_pct:.2f}%")
                    report.append(f"评分: {review.overall_score:.2f}/10")
                    report.append(f"结论: {review.conclusion}")
                    for dim, score in review.dimensions.items():
                        if score.issues:
                            report.append(f"- {dim.value}: 发现问题 - {'; '.join(score.issues[:2])}")
        
        return "\n".join(report)
    
    def generate_learning_report(
        self,
        learner: Any,
        config: Optional[ReportConfig] = None
    ) -> str:
        """
        生成学习洞察报告
        
        Args:
            learner: FeedbackLearner对象
            config: 报告配置
            
        Returns:
            格式化报告字符串
        """
        cfg = config or ReportConfig()
        
        report = []
        report.append(f"# AI学习洞察报告")
        report.append(f"\n**生成时间**: {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M:%S UTC')}")
        report.append("")
        
        insights = learner.get_top_insights(10)
        recommendations = learner.get_actionable_recommendations()
        
        report.append(f"## 📚 洞察汇总")
        report.append(f"- 生成洞察数: {len(learner.insights)}")
        report.append(f"- 可行建议数: {len(recommendations)}")
        report.append("")
        
        report.append("## 💡 最佳洞察 (Top 10)")
        for i, insight in enumerate(insights, 1):
            report.append(f"\n### {i}. {insight.title}")
            report.append(f"**类型**: {insight.insight_type.value}")
            report.append(f"**置信度**: {insight.confidence:.0%}")
            report.append(f"**支持交易数**: {insight.supporting_trades}")
            report.append(f"\n{insight.description}")
            report.append(f"\n**证据**:")
            for evidence in insight.evidence[:3]:
                report.append(f"- {evidence}")
            report.append(f"\n**建议**: {insight.recommendation}")
        
        if recommendations:
            report.append("\n## 🎯 可行建议")
            for rec in recommendations:
                priority_emoji = "🔴" if rec.priority == "high" else "🟡" if rec.priority == "medium" else "🟢"
                report.append(f"\n{priority_emoji} **{rec.category}** [{rec.priority.upper()}]")
                report.append(f"**行动**: {rec.action}")
                report.append(f"**理由**: {rec.rationale}")
                report.append(f"**预期影响**: {rec.expected_impact}")
                report.append(f"**实施方法**: {rec.implementation}")
        
        return "\n".join(report)
    
    def generate_comprehensive_report(
        self,
        metrics: Any,
        reviews: List[Any],
        learner: Any,
        config: Optional[ReportConfig] = None
    ) -> str:
        """
        生成综合报告
        
        Args:
            metrics: PerformanceMetrics对象
            reviews: TradeReview列表
            learner: FeedbackLearner对象
            config: 报告配置
            
        Returns:
            格式化报告字符串
        """
        cfg = config or ReportConfig()
        
        report = []
        report.append(f"# 📈 Hyper-Alpha-Arena 交易综合分析报告")
        report.append(f"\n**生成时间**: {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M:%S UTC')}")
        report.append(f"**分析期间**: {metrics.period_start.strftime('%Y-%m-%d')} 至 {metrics.period_end.strftime('%Y-%m-%d')}")
        report.append("")
        
        report.append("=" * 60)
        report.append("")
        
        perf_report = self.generate_performance_report(metrics, ReportConfig(title="性能概览"))
        report.append(perf_report)
        
        report.append("\n" + "=" * 60 + "\n")
        
        review_report = self.generate_review_report(reviews, ReportConfig(title="复盘分析"))
        report.append(review_report)
        
        report.append("\n" + "=" * 60 + "\n")
        
        learning_report = self.generate_learning_report(learner, ReportConfig(title="学习洞察"))
        report.append(learning_report)
        
        report.append("\n" + "=" * 60)
        report.append("\n**报告结束**")
        report.append(f"Generated by Hyper-Alpha-Arena Learning Feedback System")
        
        return "\n".join(report)
    
    def _rate_metric(
        self,
        value: float,
        poor_threshold: float,
        acceptable_threshold: float,
        good_threshold: float
    ) -> str:
        """评级指标"""
        if value >= good_threshold:
            return "⭐⭐⭐"
        elif value >= acceptable_threshold:
            return "⭐⭐"
        elif value >= poor_threshold:
            return "⭐"
        else:
            return "❌"
    
    def export_to_json(self, report_data: Dict, path: str):
        """导出报告为JSON"""
        try:
            with open(path, 'w', encoding='utf-8') as f:
                json.dump(report_data, f, ensure_ascii=False, indent=2, default=str)
            logger.info(f"[ReportGenerator] Report exported to {path}")
        except Exception as e:
            logger.error(f"[ReportGenerator] Failed to export report: {e}")
    
    def export_to_html(self, report: str, path: str):
        """导出报告为HTML"""
        html_template = f"""
<!DOCTYPE html>
<html>
<head>
    <meta charset="utf-8">
    <title>交易分析报告</title>
    <style>
        body {{ font-family: Arial, sans-serif; max-width: 1200px; margin: 0 auto; padding: 20px; }}
        table {{ border-collapse: collapse; width: 100%; margin: 10px 0; }}
        th, td {{ border: 1px solid #ddd; padding: 8px; text-align: left; }}
        th {{ background-color: #4CAF50; color: white; }}
        tr:nth-child(even) {{ background-color: #f2f2f2; }}
        h1 {{ color: #333; }}
        h2 {{ color: #4CAF50; border-bottom: 2px solid #4CAF50; padding-bottom: 10px; }}
        h3 {{ color: #2196F3; }}
        code {{ background-color: #f5f5f5; padding: 2px 5px; border-radius: 3px; }}
        pre {{ background-color: #f5f5f5; padding: 10px; border-radius: 5px; overflow-x: auto; }}
    </style>
</head>
<body>
{self._markdown_to_html(report)}
</body>
</html>
"""
        try:
            with open(path, 'w', encoding='utf-8') as f:
                f.write(html_template)
            logger.info(f"[ReportGenerator] HTML report exported to {path}")
        except Exception as e:
            logger.error(f"[ReportGenerator] Failed to export HTML report: {e}")
    
    def _markdown_to_html(self, markdown: str) -> str:
        """Markdown转HTML"""
        import re
        html = markdown
        
        html = re.sub(r'^# (.*?)$', r'<h1>\1</h1>', html, flags=re.MULTILINE)
        html = re.sub(r'^## (.*?)$', r'<h2>\1</h2>', html, flags=re.MULTILINE)
        html = re.sub(r'^### (.*?)$', r'<h3>\1</h3>', html, flags=re.MULTILINE)
        
        html = re.sub(r'\*\*(.*?)\*\*', r'<strong>\1</strong>', html)
        html = re.sub(r'\*(.*?)\*', r'<em>\1</em>', html)
        html = re.sub(r'`(.*?)`', r'<code>\1</code>', html)
        
        html = re.sub(r'\| (.*?) \|', lambda m: f'<tr>{"".join(f"<td>{c.strip()}</td>" for c in m.group(1).split("|") if c.strip())}</tr>', html)
        html = re.sub(r'(-{3,})', '', html)
        
        html = re.sub(r'^\- (.*?)$', r'<li>\1</li>', html, flags=re.MULTILINE)
        html = re.sub(r'(<li>.*</li>\n?)+', lambda m: f'<ul>{m.group(0)}</ul>', html)
        
        html = html.replace('\n', '<br>')
        
        return html


# 全局实例
_report_generator: Optional[ReportGenerator] = None


def get_report_generator() -> ReportGenerator:
    """获取全局报告生成器"""
    global _report_generator
    if _report_generator is None:
        _report_generator = ReportGenerator()
    return _report_generator
