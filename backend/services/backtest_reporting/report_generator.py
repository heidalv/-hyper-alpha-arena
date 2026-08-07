"""
ATAS V2 回测报告生成器

支持HTML/PDF/JSON多种格式
"""
from dataclasses import asdict
from datetime import datetime
from enum import Enum
from typing import Optional, Dict, Any
import json
from pathlib import Path


class ReportFormat(Enum):
    """报告格式"""
    HTML = "html"
    JSON = "json"
    PDF = "pdf"


class BacktestReportGenerator:
    """回测报告生成器"""
    
    def __init__(self):
        pass
    
    def generate(
        self,
        backtest_result: Any,
        format: ReportFormat = ReportFormat.HTML,
        output_path: Optional[str] = None
    ) -> str:
        """
        生成回测报告
        
        Args:
            backtest_result: 回测结果
            format: 报告格式
            output_path: 输出路径
            
        Returns:
            str: 报告内容或文件路径
        """
        if format == ReportFormat.HTML:
            return self._generate_html(backtest_result, output_path)
        elif format == ReportFormat.JSON:
            return self._generate_json(backtest_result, output_path)
        elif format == ReportFormat.PDF:
            return self._generate_pdf(backtest_result, output_path)
        else:
            raise ValueError(f"Unsupported format: {format}")
    
    def _generate_html(self, result: Any, output_path: Optional[str]) -> str:
        """生成HTML报告"""
        html = f"""
<!DOCTYPE html>
<html>
<head>
    <meta charset="UTF-8">
    <title>回测报告</title>
    <style>
        body {{ font-family: Arial, sans-serif; margin: 20px; }}
        h1 {{ color: #333; }}
        h2 {{ color: #666; margin-top: 30px; }}
        table {{ border-collapse: collapse; width: 100%; margin: 20px 0; }}
        th, td {{ border: 1px solid #ddd; padding: 12px; text-align: left; }}
        th {{ background-color: #4CAF50; color: white; }}
        tr:nth-child(even) {{ background-color: #f2f2f2; }}
        .positive {{ color: green; }}
        .negative {{ color: red; }}
    </style>
</head>
<body>
    <h1>策略回测报告</h1>
    
    <h2>基本信息</h2>
    <table>
        <tr><th>项目</th><th>数值</th></tr>
        <tr><td>开始日期</td><td>{result.start_date}</td></tr>
        <tr><td>结束日期</td><td>{result.end_date}</td></tr>
        <tr><td>初始资金</td><td>${result.initial_capital:,.2f}</td></tr>
        <tr><td>最终资金</td><td>${result.final_capital:,.2f}</td></tr>
    </table>
    
    <h2>收益指标</h2>
    <table>
        <tr><th>指标</th><th>数值</th></tr>
        <tr><td>总收益率</td><td class="{'positive' if result.total_return > 0 else 'negative'}">{result.total_return*100:.2f}%</td></tr>
        <tr><td>年化收益率</td><td class="{'positive' if result.annualized_return > 0 else 'negative'}">{result.annualized_return*100:.2f}%</td></tr>
    </table>
    
    <h2>风险指标</h2>
    <table>
        <tr><th>指标</th><th>数值</th></tr>
        <tr><td>最大回撤</td><td class="negative">{result.max_drawdown*100:.2f}%</td></tr>
        <tr><td>夏普比率</td><td>{result.sharpe_ratio:.2f}</td></tr>
        <tr><td>索提诺比率</td><td>{result.sortino_ratio:.2f}</td></tr>
        <tr><td>卡玛比率</td><td>{result.calmar_ratio:.2f}</td></tr>
    </table>
    
    <h2>交易统计</h2>
    <table>
        <tr><th>指标</th><th>数值</th></tr>
        <tr><td>总交易次数</td><td>{result.total_trades}</td></tr>
        <tr><td>盈利次数</td><td class="positive">{result.winning_trades}</td></tr>
        <tr><td>亏损次数</td><td class="negative">{result.losing_trades}</td></tr>
        <tr><td>胜率</td><td>{result.win_rate*100:.2f}%</td></tr>
        <tr><td>平均盈利</td><td class="positive">${result.avg_win:.2f}</td></tr>
        <tr><td>平均亏损</td><td class="negative">${result.avg_loss:.2f}</td></tr>
        <tr><td>盈亏比</td><td>{result.profit_factor:.2f}</td></tr>
    </table>
    
    <p style="margin-top: 50px; color: #999; font-size: 12px;">
        报告生成时间: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}
    </p>
</body>
</html>
"""
        
        if output_path:
            Path(output_path).parent.mkdir(parents=True, exist_ok=True)
            with open(output_path, 'w', encoding='utf-8') as f:
                f.write(html)
            return output_path
        
        return html
    
    def _generate_json(self, result: Any, output_path: Optional[str]) -> str:
        """生成JSON报告"""
        data = {
            'basic_info': {
                'start_date': str(result.start_date),
                'end_date': str(result.end_date),
                'initial_capital': result.initial_capital,
                'final_capital': result.final_capital,
            },
            'returns': {
                'total_return': result.total_return,
                'annualized_return': result.annualized_return,
            },
            'risk': {
                'max_drawdown': result.max_drawdown,
                'sharpe_ratio': result.sharpe_ratio,
                'sortino_ratio': result.sortino_ratio,
                'calmar_ratio': result.calmar_ratio,
            },
            'trades': {
                'total_trades': result.total_trades,
                'winning_trades': result.winning_trades,
                'losing_trades': result.losing_trades,
                'win_rate': result.win_rate,
                'avg_win': result.avg_win,
                'avg_loss': result.avg_loss,
                'profit_factor': result.profit_factor,
            },
            'generated_at': datetime.now().isoformat()
        }
        
        json_str = json.dumps(data, indent=2, ensure_ascii=False)
        
        if output_path:
            Path(output_path).parent.mkdir(parents=True, exist_ok=True)
            with open(output_path, 'w', encoding='utf-8') as f:
                f.write(json_str)
            return output_path
        
        return json_str
    
    def _generate_pdf(self, result: Any, output_path: Optional[str]) -> str:
        """生成PDF报告"""
        try:
            from weasyprint import HTML
            
            html_content = self._generate_html(result, None)
            
            if output_path:
                Path(output_path).parent.mkdir(parents=True, exist_ok=True)
                HTML(string=html_content).write_pdf(output_path)
                return output_path
            else:
                raise ValueError("output_path is required for PDF format")
        
        except ImportError:
            raise ImportError("weasyprint is required for PDF generation")


def generate_backtest_report(
    backtest_result: Any,
    format: ReportFormat = ReportFormat.HTML,
    output_path: Optional[str] = None
) -> str:
    """便捷函数：生成回测报告"""
    generator = BacktestReportGenerator()
    return generator.generate(backtest_result, format, output_path)
