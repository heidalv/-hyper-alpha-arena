"""
ATAS V2 健康度评分

修复记录：
- 修复 stability 评分可能为负数的溢出问题
- 修复 performance 评分对大/小账户的归一化问题
- 使用 current_drawdown 真实数据（不再永远为0）
- 使用真实 volatility 数据（不再硬编码 0.02）
"""
from dataclasses import dataclass
from typing import Dict


@dataclass
class HealthScore:
    overall: float
    performance: float
    risk: float
    stability: float
    liquidity: float


class HealthScoreCalculator:
    """
    账户健康度评分器
    
    各维度评分范围 [0, 100]：
    - performance: 基于日盈亏比例（相对于账户总值）
    - risk: 基于回撤深度
    - stability: 基于波动率
    - liquidity: 基于现金比例
    - overall: 加权平均
    """
    
    # 权重配置
    WEIGHTS = {
        'performance': 0.25,
        'risk': 0.30,
        'stability': 0.25,
        'liquidity': 0.20,
    }
    
    def calculate(self, portfolio: Dict, metrics: Dict) -> HealthScore:
        """
        计算健康度评分
        
        Args:
            portfolio: 投资组合数据，包含 total_value, daily_pnl, current_drawdown, cash_ratio
            metrics: 市场指标数据，包含 volatility
        """
        total_value = portfolio.get('total_value', 0)
        
        # === Performance 评分 ===
        # 基于日盈亏占总资产的比例进行归一化
        daily_pnl = portfolio.get('daily_pnl', 0)
        if total_value > 0:
            # 日盈亏比例：+1% -> 满分, -1% -> 0 分
            pnl_ratio = daily_pnl / total_value
            # 将 [-0.01, +0.01] 映射到 [0, 100]
            perf = 50 + pnl_ratio * 5000  # 0.01 -> +50, -0.01 -> -50
        else:
            perf = 50  # 无数据时给中间分
        perf = min(100, max(0, perf))
        
        # === Risk 评分 ===
        # 基于回撤深度：0% -> 100分, 5% -> 75分, 10% -> 50分, 20% -> 0分
        drawdown = portfolio.get('current_drawdown', 0)
        risk = max(0, min(100, 100 - drawdown * 500))
        
        # === Stability 评分 ===
        # 基于波动率：0% -> 100分, 2% -> 80分, 5% -> 50分, 10%+ -> 0分
        volatility = metrics.get('volatility', 0)
        stability = max(0, min(100, 100 - volatility * 1000))
        
        # === Liquidity 评分 ===
        # 基于现金比例：100% -> 100分, 50% -> 75分, 0% -> 25分
        cash_ratio = portfolio.get('cash_ratio', 0.5)
        liquidity = max(0, min(100, 25 + cash_ratio * 75))
        
        # === Overall 加权平均 ===
        overall = (
            perf * self.WEIGHTS['performance'] +
            risk * self.WEIGHTS['risk'] +
            stability * self.WEIGHTS['stability'] +
            liquidity * self.WEIGHTS['liquidity']
        )
        overall = round(min(100, max(0, overall)), 1)
        
        return HealthScore(
            overall=overall,
            performance=round(perf, 1),
            risk=round(risk, 1),
            stability=round(stability, 1),
            liquidity=round(liquidity, 1)
        )


def calculate_health_score(portfolio: Dict, metrics: Dict) -> HealthScore:
    calc = HealthScoreCalculator()
    return calc.calculate(portfolio, metrics)
