"""
ATAS V2 风险控制器

多层风控体系：账户级、策略级、交易级
"""
from dataclasses import dataclass
from datetime import datetime
from enum import Enum
from typing import Dict, List, Optional, Any
import numpy as np


class RiskLevel(Enum):
    """风险等级"""
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"
    CRITICAL = "critical"


@dataclass
class RiskCheckResult:
    """风险检查结果"""
    passed: bool  # 是否通过
    risk_level: RiskLevel  # 风险等级
    violations: List[str]  # 违规项
    warnings: List[str]  # 警告项
    metrics: Dict[str, float]  # 风险指标


class RiskController:
    """风险控制器"""
    
    def __init__(self, config: Optional[Dict[str, Any]] = None):
        self.config = config or self._default_config()
        self.violation_history: List[RiskCheckResult] = []
    
    def _default_config(self) -> Dict[str, Any]:
        """默认风控配置"""
        return {
            # 账户级风控
            'max_total_exposure': 0.95,  # 最大总仓位
            'max_drawdown_limit': 0.20,  # 最大回撤限制
            'daily_loss_limit': 0.05,  # 日亏损限制
            
            # 策略级风控
            'max_strategy_exposure': 0.30,  # 单策略最大仓位
            'max_strategy_drawdown': 0.15,  # 策略最大回撤
            
            # 交易级风控
            'max_position_size': 0.10,  # 单笔最大仓位
            'max_leverage': 3.0,  # 最大杠杆
            'max_orders_per_day': 100,  # 日最大交易次数
            
            # 集中度风控
            'max_correlation': 0.8,  # 最大相关性
            'max_sector_exposure': 0.40,  # 单板块最大仓位
        }
    
    def check_risk(
        self,
        portfolio: Dict[str, Any],
        new_order: Optional[Dict[str, Any]] = None
    ) -> RiskCheckResult:
        """
        全面风险检查
        
        Args:
            portfolio: 投资组合状态
            new_order: 新订单（可选）
            
        Returns:
            RiskCheckResult: 风险检查结果
        """
        violations = []
        warnings = []
        metrics = {}
        
        # 1. 账户级风控检查
        account_violations, account_metrics = self._check_account_risk(portfolio)
        violations.extend(account_violations)
        metrics.update(account_metrics)
        
        # 2. 仓位检查
        position_violations, position_metrics = self._check_position_risk(portfolio)
        violations.extend(position_violations)
        metrics.update(position_metrics)
        
        # 3. 如果有新订单，检查订单风险
        if new_order:
            order_violations, order_metrics = self._check_order_risk(portfolio, new_order)
            violations.extend(order_violations)
            metrics.update(order_metrics)
        
        # 4. 评估风险等级
        risk_level = self._assess_risk_level(metrics, violations)
        
        # 5. 生成警告
        warnings = self._generate_warnings(metrics)
        
        # 6. 判断是否通过
        passed = len(violations) == 0 and risk_level != RiskLevel.CRITICAL
        
        result = RiskCheckResult(
            passed=passed,
            risk_level=risk_level,
            violations=violations,
            warnings=warnings,
            metrics=metrics
        )
        
        if not passed:
            self.violation_history.append(result)
        
        return result
    
    def _check_account_risk(
        self,
        portfolio: Dict[str, Any]
    ) -> tuple[List[str], Dict[str, float]]:
        """账户级风控检查"""
        violations = []
        metrics = {}
        
        total_value = portfolio.get('total_value', 0)
        capital = portfolio.get('capital', 0)
        
        # 计算总仓位
        total_exposure = 1 - (capital / total_value) if total_value > 0 else 0
        metrics['total_exposure'] = total_exposure
        
        if total_exposure > self.config['max_total_exposure']:
            violations.append(
                f"总仓位超限: {total_exposure:.2%} > {self.config['max_total_exposure']:.2%}"
            )
        
        # 检查回撤
        if 'max_drawdown' in portfolio:
            drawdown = portfolio['max_drawdown']
            metrics['current_drawdown'] = drawdown
            
            if drawdown > self.config['max_drawdown_limit']:
                violations.append(
                    f"回撤超限: {drawdown:.2%} > {self.config['max_drawdown_limit']:.2%}"
                )
        
        # 检查日亏损
        if 'daily_pnl' in portfolio:
            daily_pnl = portfolio['daily_pnl']
            daily_loss_pct = abs(daily_pnl / total_value) if total_value > 0 else 0
            metrics['daily_loss_pct'] = daily_loss_pct
            
            if daily_pnl < 0 and daily_loss_pct > self.config['daily_loss_limit']:
                violations.append(
                    f"日亏损超限: {daily_loss_pct:.2%} > {self.config['daily_loss_limit']:.2%}"
                )
        
        return violations, metrics
    
    def _check_position_risk(
        self,
        portfolio: Dict[str, Any]
    ) -> tuple[List[str], Dict[str, float]]:
        """仓位风险检查"""
        violations = []
        metrics = {}
        
        positions = portfolio.get('positions', {})
        total_value = portfolio.get('total_value', 0)
        
        if not positions or total_value == 0:
            return violations, metrics
        
        # 检查单个仓位
        for symbol, position in positions.items():
            position_value = abs(position.get('quantity', 0) * position.get('current_price', 0))
            position_pct = position_value / total_value
            
            metrics[f'{symbol}_exposure'] = position_pct
            
            if position_pct > self.config['max_position_size']:
                violations.append(
                    f"{symbol} 仓位超限: {position_pct:.2%} > {self.config['max_position_size']:.2%}"
                )
        
        # 检查杠杆
        total_position_value = sum(
            abs(p.get('quantity', 0) * p.get('current_price', 0))
            for p in positions.values()
        )
        leverage = total_position_value / total_value if total_value > 0 else 0
        metrics['leverage'] = leverage
        
        if leverage > self.config['max_leverage']:
            violations.append(
                f"杠杆超限: {leverage:.2f}x > {self.config['max_leverage']:.2f}x"
            )
        
        return violations, metrics
    
    def _check_order_risk(
        self,
        portfolio: Dict[str, Any],
        order: Dict[str, Any]
    ) -> tuple[List[str], Dict[str, float]]:
        """订单风险检查"""
        violations = []
        metrics = {}
        
        total_value = portfolio.get('total_value', 0)
        
        # 计算订单金额
        order_value = order.get('quantity', 0) * order.get('price', 0)
        order_pct = order_value / total_value if total_value > 0 else 0
        
        metrics['order_size_pct'] = order_pct
        
        if order_pct > self.config['max_position_size']:
            violations.append(
                f"订单金额超限: {order_pct:.2%} > {self.config['max_position_size']:.2%}"
            )
        
        # 检查日交易次数
        if 'orders_today' in portfolio:
            orders_count = portfolio['orders_today']
            metrics['orders_today'] = orders_count
            
            if orders_count >= self.config['max_orders_per_day']:
                violations.append(
                    f"日交易次数超限: {orders_count} >= {self.config['max_orders_per_day']}"
                )
        
        return violations, metrics
    
    def _assess_risk_level(
        self,
        metrics: Dict[str, float],
        violations: List[str]
    ) -> RiskLevel:
        """评估风险等级"""
        if len(violations) >= 3:
            return RiskLevel.CRITICAL
        elif len(violations) >= 1:
            return RiskLevel.HIGH
        
        # 基于指标评估
        total_exposure = metrics.get('total_exposure', 0)
        leverage = metrics.get('leverage', 0)
        drawdown = metrics.get('current_drawdown', 0)
        
        risk_score = 0
        if total_exposure > 0.8:
            risk_score += 1
        if leverage > 2.0:
            risk_score += 1
        if drawdown > 0.15:
            risk_score += 1
        
        if risk_score >= 2:
            return RiskLevel.MEDIUM
        else:
            return RiskLevel.LOW
    
    def _generate_warnings(self, metrics: Dict[str, float]) -> List[str]:
        """生成警告信息"""
        warnings = []
        
        # 仓位警告
        total_exposure = metrics.get('total_exposure', 0)
        if 0.8 < total_exposure <= self.config['max_total_exposure']:
            warnings.append(f"总仓位较高: {total_exposure:.2%}")
        
        # 杠杆警告
        leverage = metrics.get('leverage', 0)
        if 2.0 < leverage <= self.config['max_leverage']:
            warnings.append(f"杠杆较高: {leverage:.2f}x")
        
        # 回撤警告
        drawdown = metrics.get('current_drawdown', 0)
        if 0.15 < drawdown <= self.config['max_drawdown_limit']:
            warnings.append(f"回撤较大: {drawdown:.2%}")
        
        return warnings
    
    def get_risk_summary(self) -> Dict[str, Any]:
        """获取风险摘要"""
        return {
            'config': self.config,
            'total_violations': len(self.violation_history),
            'recent_violations': self.violation_history[-10:] if self.violation_history else []
        }


def check_risk(
    portfolio: Dict[str, Any],
    new_order: Optional[Dict[str, Any]] = None,
    config: Optional[Dict[str, Any]] = None
) -> RiskCheckResult:
    """便捷函数：风险检查"""
    controller = RiskController(config)
    return controller.check_risk(portfolio, new_order)
