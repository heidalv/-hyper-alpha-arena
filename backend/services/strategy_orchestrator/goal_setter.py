"""
Goal Setter - 目标设定模块

提供交易目标设定和追踪功能：
1. 夏普比率最大化目标
2. 回撤控制目标
3. 收益目标分解
4. 风险预算分配

Author: Hyper-Alpha-Arena
"""

import numpy as np
from typing import Dict, List, Optional, Any, Tuple
from dataclasses import dataclass, field
from enum import Enum
from datetime import datetime, timedelta
import logging

logger = logging.getLogger(__name__)


class GoalType(str, Enum):
    """目标类型"""
    SHARPE_RATIO = "sharpe_ratio"
    RETURN = "return"
    MAX_DRAWDOWN = "max_drawdown"
    WIN_RATE = "win_rate"
    PROFIT_FACTOR = "profit_factor"
    CALMAR_RATIO = "calmar_ratio"
    SORTINO_RATIO = "sortino_ratio"


class GoalPriority(str, Enum):
    """目标优先级"""
    CRITICAL = "critical"  # 必须达成
    HIGH = "high"          # 高优先级
    MEDIUM = "medium"      # 中等优先级
    LOW = "low"            # 低优先级


class GoalStatus(str, Enum):
    """目标状态"""
    ON_TRACK = "on_track"      # 进度正常
    AT_RISK = "at_risk"        # 有风险
    BEHIND = "behind"          # 落后
    ACHIEVED = "achieved"      # 已达成
    FAILED = "failed"          # 已失败


@dataclass
class TradingGoal:
    """交易目标"""
    goal_type: GoalType
    target_value: float
    current_value: float = 0.0
    priority: GoalPriority = GoalPriority.MEDIUM
    deadline: Optional[datetime] = None
    status: GoalStatus = GoalStatus.ON_TRACK
    
    # 约束条件
    min_acceptable: Optional[float] = None
    max_acceptable: Optional[float] = None
    
    # 元数据
    created_at: datetime = field(default_factory=datetime.now)
    updated_at: datetime = field(default_factory=datetime.now)
    
    def progress(self) -> float:
        """计算进度百分比"""
        if self.target_value == 0:
            return 0.0 if self.current_value == 0 else 100.0
        return (self.current_value / self.target_value) * 100
    
    def is_achievable(self, remaining_days: int, avg_daily_progress: float) -> bool:
        """判断目标是否可达成"""
        remaining_value = self.target_value - self.current_value
        projected_progress = remaining_days * avg_daily_progress
        return projected_progress >= remaining_value


@dataclass
class GoalAllocation:
    """目标分配"""
    symbol: str
    return_target: float
    risk_budget: float
    max_drawdown: float
    position_limit: float


@dataclass
class GoalReport:
    """目标报告"""
    goals: List[TradingGoal]
    overall_progress: float
    critical_goals_status: Dict[str, GoalStatus]
    recommendations: List[str]
    risk_alerts: List[str]
    timestamp: datetime = field(default_factory=datetime.now)


class GoalCalculator:
    """目标计算器"""
    
    @staticmethod
    def calculate_sharpe_ratio(
        returns: List[float],
        risk_free_rate: float = 0.0,
        annualization_factor: float = 365
    ) -> float:
        """计算夏普比率"""
        if not returns or len(returns) < 2:
            return 0.0
        
        returns_arr = np.array(returns)
        excess_returns = returns_arr - risk_free_rate / annualization_factor
        
        mean_excess = np.mean(excess_returns)
        std_excess = np.std(excess_returns)
        
        if std_excess == 0:
            return 0.0
        
        sharpe = (mean_excess / std_excess) * np.sqrt(annualization_factor)
        return float(sharpe)
    
    @staticmethod
    def calculate_sortino_ratio(
        returns: List[float],
        risk_free_rate: float = 0.0,
        annualization_factor: float = 365
    ) -> float:
        """计算Sortino比率"""
        if not returns or len(returns) < 2:
            return 0.0
        
        returns_arr = np.array(returns)
        excess_returns = returns_arr - risk_free_rate / annualization_factor
        
        mean_excess = np.mean(excess_returns)
        negative_returns = excess_returns[excess_returns < 0]
        
        if len(negative_returns) == 0:
            return float('inf') if mean_excess > 0 else 0.0
        
        downside_std = np.std(negative_returns)
        if downside_std == 0:
            return 0.0
        
        sortino = (mean_excess / downside_std) * np.sqrt(annualization_factor)
        return float(sortino)
    
    @staticmethod
    def calculate_calmar_ratio(
        total_return: float,
        max_drawdown: float,
        period_years: float = 1.0
    ) -> float:
        """计算Calmar比率"""
        if max_drawdown == 0:
            return float('inf') if total_return > 0 else 0.0
        
        annualized_return = total_return / period_years
        return annualized_return / abs(max_drawdown)
    
    @staticmethod
    def calculate_max_drawdown(equity_curve: List[float]) -> float:
        """计算最大回撤"""
        if not equity_curve:
            return 0.0
        
        peak = equity_curve[0]
        max_dd = 0.0
        
        for value in equity_curve:
            if value > peak:
                peak = value
            drawdown = (peak - value) / peak if peak > 0 else 0.0
            max_dd = max(max_dd, drawdown)
        
        return max_dd
    
    @staticmethod
    def calculate_profit_factor(wins: List[float], losses: List[float]) -> float:
        """计算盈利因子"""
        total_wins = sum(abs(w) for w in wins)
        total_losses = sum(abs(l) for l in losses)
        
        if total_losses == 0:
            return float('inf') if total_wins > 0 else 0.0
        
        return total_wins / total_losses


class GoalSetter:
    """目标设定器"""
    
    def __init__(self):
        self.goals: Dict[str, TradingGoal] = {}
        self.calculator = GoalCalculator()
        self.performance_history: List[Dict[str, float]] = []
        
        # 默认目标模板
        self.goal_templates = self._define_goal_templates()
    
    def _define_goal_templates(self) -> Dict[str, Dict[str, Any]]:
        """定义目标模板"""
        return {
            "conservative": {
                "sharpe_ratio": 1.5,
                "max_drawdown": 0.10,
                "win_rate": 0.55,
                "monthly_return": 0.03
            },
            "moderate": {
                "sharpe_ratio": 2.0,
                "max_drawdown": 0.15,
                "win_rate": 0.50,
                "monthly_return": 0.05
            },
            "aggressive": {
                "sharpe_ratio": 2.5,
                "max_drawdown": 0.20,
                "win_rate": 0.45,
                "monthly_return": 0.08
            },
            "high_frequency": {
                "sharpe_ratio": 3.0,
                "max_drawdown": 0.05,
                "win_rate": 0.60,
                "monthly_return": 0.02
            }
        }
    
    def set_goal(
        self,
        goal_id: str,
        goal_type: GoalType,
        target_value: float,
        priority: GoalPriority = GoalPriority.MEDIUM,
        deadline: Optional[datetime] = None,
        constraints: Optional[Dict[str, float]] = None
    ) -> TradingGoal:
        """设定目标"""
        goal = TradingGoal(
            goal_type=goal_type,
            target_value=target_value,
            priority=priority,
            deadline=deadline,
            min_acceptable=constraints.get("min") if constraints else None,
            max_acceptable=constraints.get("max") if constraints else None
        )
        
        self.goals[goal_id] = goal
        logger.info(f"Set goal '{goal_id}': {goal_type.value} = {target_value}")
        
        return goal
    
    def set_goals_from_template(
        self,
        template_name: str,
        deadline: Optional[datetime] = None
    ) -> Dict[str, TradingGoal]:
        """从模板设定目标"""
        if template_name not in self.goal_templates:
            logger.warning(f"Template '{template_name}' not found, using 'moderate'")
            template_name = "moderate"
        
        template = self.goal_templates[template_name]
        created_goals = {}
        
        goal_type_mapping = {
            "sharpe_ratio": GoalType.SHARPE_RATIO,
            "max_drawdown": GoalType.MAX_DRAWDOWN,
            "win_rate": GoalType.WIN_RATE,
            "monthly_return": GoalType.RETURN
        }
        
        for goal_name, target in template.items():
            if goal_name in goal_type_mapping:
                goal_id = f"{template_name}_{goal_name}"
                priority = GoalPriority.CRITICAL if goal_name == "max_drawdown" else GoalPriority.HIGH
                
                goal = self.set_goal(
                    goal_id=goal_id,
                    goal_type=goal_type_mapping[goal_name],
                    target_value=target,
                    priority=priority,
                    deadline=deadline
                )
                created_goals[goal_id] = goal
        
        return created_goals
    
    def update_progress(
        self,
        returns: List[float],
        equity_curve: List[float],
        trades: Optional[List[Dict[str, Any]]] = None
    ) -> None:
        """更新目标进度"""
        # 计算各项指标
        sharpe = self.calculator.calculate_sharpe_ratio(returns)
        sortino = self.calculator.calculate_sortino_ratio(returns)
        max_dd = self.calculator.calculate_max_drawdown(equity_curve)
        total_return = (equity_curve[-1] - equity_curve[0]) / equity_curve[0] if equity_curve else 0.0
        
        win_rate = 0.0
        profit_factor = 0.0
        if trades:
            wins = [t["pnl"] for t in trades if t.get("pnl", 0) > 0]
            losses = [t["pnl"] for t in trades if t.get("pnl", 0) < 0]
            win_rate = len(wins) / len(trades) if trades else 0.0
            profit_factor = self.calculator.calculate_profit_factor(wins, losses)
        
        # 更新各目标
        metric_values = {
            GoalType.SHARPE_RATIO: sharpe,
            GoalType.SORTINO_RATIO: sortino,
            GoalType.MAX_DRAWDOWN: max_dd,
            GoalType.RETURN: total_return,
            GoalType.WIN_RATE: win_rate,
            GoalType.PROFIT_FACTOR: profit_factor
        }
        
        for goal_id, goal in self.goals.items():
            if goal.goal_type in metric_values:
                goal.current_value = metric_values[goal.goal_type]
                goal.updated_at = datetime.now()
                goal.status = self._evaluate_status(goal)
        
        # 保存历史
        self.performance_history.append({
            "timestamp": datetime.now().isoformat(),
            **{g.value: v for g, v in metric_values.items()}
        })
    
    def _evaluate_status(self, goal: TradingGoal) -> GoalStatus:
        """评估目标状态"""
        progress = goal.progress()
        
        # 对于需要最小化的指标（如回撤）
        if goal.goal_type == GoalType.MAX_DRAWDOWN:
            if goal.current_value <= goal.target_value:
                return GoalStatus.ON_TRACK
            elif goal.current_value <= goal.target_value * 1.2:
                return GoalStatus.AT_RISK
            else:
                return GoalStatus.BEHIND
        
        # 对于需要最大化的指标
        if progress >= 100:
            return GoalStatus.ACHIEVED
        elif progress >= 80:
            return GoalStatus.ON_TRACK
        elif progress >= 50:
            return GoalStatus.AT_RISK
        else:
            return GoalStatus.BEHIND
    
    def allocate_risk_budget(
        self,
        total_budget: float,
        symbols: List[str],
        correlations: Optional[Dict[str, Dict[str, float]]] = None,
        expected_returns: Optional[Dict[str, float]] = None
    ) -> Dict[str, GoalAllocation]:
        """分配风险预算"""
        allocations = {}
        n_symbols = len(symbols)
        
        if n_symbols == 0:
            return allocations
        
        # 简单等权分配（如果没有预期收益信息）
        if not expected_returns:
            base_allocation = total_budget / n_symbols
            for symbol in symbols:
                allocations[symbol] = GoalAllocation(
                    symbol=symbol,
                    return_target=base_allocation * 0.05,  # 5%预期收益
                    risk_budget=base_allocation,
                    max_drawdown=0.02,  # 2%最大回撤
                    position_limit=base_allocation * 3  # 3倍杠杆上限
                )
            return allocations
        
        # 基于预期收益的分配
        total_expected = sum(max(r, 0.001) for r in expected_returns.values())
        
        for symbol in symbols:
            exp_return = expected_returns.get(symbol, 0.01)
            weight = max(exp_return, 0.001) / total_expected
            budget = total_budget * weight
            
            allocations[symbol] = GoalAllocation(
                symbol=symbol,
                return_target=budget * exp_return,
                risk_budget=budget,
                max_drawdown=0.02 * (1 + weight),  # 权重越大，允许的回撤越大
                position_limit=budget * 3
            )
        
        return allocations
    
    def generate_report(self) -> GoalReport:
        """生成目标报告"""
        goals_list = list(self.goals.values())
        
        # 计算整体进度
        if goals_list:
            overall_progress = sum(g.progress() for g in goals_list) / len(goals_list)
        else:
            overall_progress = 0.0
        
        # 关键目标状态
        critical_status = {
            goal_id: goal.status
            for goal_id, goal in self.goals.items()
            if goal.priority == GoalPriority.CRITICAL
        }
        
        # 生成建议
        recommendations = self._generate_recommendations()
        
        # 风险警告
        risk_alerts = self._generate_risk_alerts()
        
        return GoalReport(
            goals=goals_list,
            overall_progress=overall_progress,
            critical_goals_status=critical_status,
            recommendations=recommendations,
            risk_alerts=risk_alerts
        )
    
    def _generate_recommendations(self) -> List[str]:
        """生成优化建议"""
        recommendations = []
        
        for goal_id, goal in self.goals.items():
            if goal.status == GoalStatus.BEHIND:
                recommendations.append(
                    f"目标 '{goal_id}' 进度落后 ({goal.progress():.1f}%)，建议调整策略参数"
                )
            elif goal.status == GoalStatus.AT_RISK:
                recommendations.append(
                    f"目标 '{goal_id}' 存在风险，建议加强监控"
                )
        
        # 综合建议
        sharpe_goals = [g for g in self.goals.values() if g.goal_type == GoalType.SHARPE_RATIO]
        dd_goals = [g for g in self.goals.values() if g.goal_type == GoalType.MAX_DRAWDOWN]
        
        if sharpe_goals and sharpe_goals[0].current_value < 1.0:
            recommendations.append("夏普比率较低，建议优化入场时机和仓位管理")
        
        if dd_goals and dd_goals[0].current_value > dd_goals[0].target_value:
            recommendations.append("回撤超过目标，建议收紧止损或降低仓位")
        
        return recommendations
    
    def _generate_risk_alerts(self) -> List[str]:
        """生成风险警告"""
        alerts = []
        
        for goal_id, goal in self.goals.items():
            if goal.priority == GoalPriority.CRITICAL and goal.status in [GoalStatus.BEHIND, GoalStatus.FAILED]:
                alerts.append(f"[严重] 关键目标 '{goal_id}' 未达标: {goal.current_value:.4f} / {goal.target_value:.4f}")
            
            if goal.goal_type == GoalType.MAX_DRAWDOWN:
                if goal.current_value > goal.target_value * 1.5:
                    alerts.append(f"[严重] 回撤超过目标50%以上: {goal.current_value:.2%}")
        
        return alerts
    
    def get_goal_summary(self) -> Dict[str, Any]:
        """获取目标摘要"""
        summary = {
            "total_goals": len(self.goals),
            "achieved": sum(1 for g in self.goals.values() if g.status == GoalStatus.ACHIEVED),
            "on_track": sum(1 for g in self.goals.values() if g.status == GoalStatus.ON_TRACK),
            "at_risk": sum(1 for g in self.goals.values() if g.status == GoalStatus.AT_RISK),
            "behind": sum(1 for g in self.goals.values() if g.status == GoalStatus.BEHIND),
            "goals": {
                goal_id: {
                    "type": goal.goal_type.value,
                    "target": goal.target_value,
                    "current": goal.current_value,
                    "progress": goal.progress(),
                    "status": goal.status.value
                }
                for goal_id, goal in self.goals.items()
            }
        }
        return summary


# 全局实例
_goal_setter: Optional[GoalSetter] = None


def get_goal_setter() -> GoalSetter:
    """获取目标设定器实例"""
    global _goal_setter
    if _goal_setter is None:
        _goal_setter = GoalSetter()
    return _goal_setter


def set_trading_goals(
    template: str = "moderate",
    deadline: Optional[datetime] = None
) -> Dict[str, TradingGoal]:
    """设定交易目标"""
    return get_goal_setter().set_goals_from_template(template, deadline)


def get_goal_report() -> GoalReport:
    """获取目标报告"""
    return get_goal_setter().generate_report()
