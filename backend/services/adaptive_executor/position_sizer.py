"""
Smart Position Sizer - 智能仓位管理

提供基于多种风险模型的仓位计算：
1. Kelly Criterion (凯利公式)
2. 波动率调整仓位
3. 回撤保护
4. 相关性分散
5. 最大风险限制

Author: Hyper-Alpha-Arena
"""

import logging
from typing import Dict, List, Optional, Tuple
from dataclasses import dataclass, field
import numpy as np

logger = logging.getLogger(__name__)


@dataclass
class PositionSizeResult:
    """仓位计算结果"""
    size: float  # 仓位大小（合约数或金额）
    size_pct: float  # 仓位比例
    risk_amount: float  # 风险金额
    risk_pct: float  # 风险比例
    leverage: float  # 建议杠杆
    kelly_pct: float  # Kelly百分比
    confidence: float  # 置信度
    adjustment_reasons: List[str] = field(default_factory=list)
    warnings: List[str] = field(default_factory=list)


@dataclass
class KellyConfig:
    """Kelly公式配置"""
    base_kelly: float = 0.25  # 基础Kelly分数
    kelly_fraction: float = 0.5  # Kelly分数使用比例(半Kelly)
    max_kelly_pct: float = 0.15  # 最大Kelly仓位比例
    min_kelly_pct: float = 0.01  # 最小Kelly仓位比例


@dataclass
class VolatilityConfig:
    """波动率配置"""
    base_volatility: float = 0.5  # 基准波动率
    vol_lookback: int = 14  # 波动率回溯期
    max_position_pct: float = 0.2  # 最大单品种仓位比例
    min_position_pct: float = 0.01  # 最小仓位比例
    inverse_vol_weighting: bool = True  # 波动率倒数加权


@dataclass
class DrawdownConfig:
    """回撤配置"""
    max_drawdown_pct: float = 0.1  # 最大回撤阈值
    drawdown_reduction: float = 0.5  # 回撤时仓位缩减比例
    recovery_mode: bool = True  # 恢复模式
    recovery_threshold: float = 0.03  # 恢复阈值


@dataclass
class CorrelationConfig:
    """相关性配置"""
    correlation_threshold: float = 0.7  # 相关性阈值
    max_correlated_exposure: float = 0.3  # 相关品种最大总暴露
    default_correlation: Dict[str, Dict[str, float]] = field(default_factory=dict)


class SmartPositionSizer:
    """
    智能仓位管理器
    
    综合考虑多种风险因素，
    计算最优仓位大小
    """
    
    def __init__(
        self,
        kelly_config: Optional[KellyConfig] = None,
        vol_config: Optional[VolatilityConfig] = None,
        drawdown_config: Optional[DrawdownConfig] = None,
        correlation_config: Optional[CorrelationConfig] = None
    ):
        self.kelly = kelly_config or KellyConfig()
        self.volatility = vol_config or VolatilityConfig()
        self.drawdown = drawdown_config or DrawdownConfig()
        self.correlation = correlation_config or CorrelationConfig()
        
        self.current_drawdown: float = 0.0
        self.total_equity: float = 100000.0
        self.positions: Dict[str, Dict] = {}
    
    def set_total_equity(self, equity: float):
        """设置总资金"""
        self.total_equity = equity
        logger.info(f"[PositionSizer] Total equity set to {equity:,.2f}")
    
    def set_current_drawdown(self, drawdown: float):
        """设置当前回撤"""
        self.current_drawdown = drawdown
        logger.info(f"[PositionSizer] Current drawdown: {drawdown:.2%}")
    
    def calculate_kelly_size(
        self,
        win_rate: float,
        avg_win: float,
        avg_loss: float,
        confidence: float = 1.0
    ) -> float:
        """
        计算Kelly仓位
        
        Kelly = W - (1-W)/R
        其中 W=胜率, R=盈亏比
        
        Returns:
            Kelly分数 (0-1)
        """
        if win_rate <= 0 or avg_loss <= 0:
            return 0.0
        
        reward_risk_ratio = avg_win / avg_loss if avg_loss > 0 else 1.0
        
        kelly = win_rate - ((1 - win_rate) / reward_risk_ratio)
        
        kelly = max(0, min(kelly, self.kelly.max_kelly_pct))
        
        if confidence < 1.0:
            kelly *= confidence
        
        adjusted_kelly = kelly * self.kelly.kelly_fraction
        
        return max(adjusted_kelly, self.kelly.min_kelly_pct)
    
    def calculate_volatility_adjusted_size(
        self,
        entry_price: float,
        stop_loss: float,
        current_volatility: float,
        max_risk_pct: float = 0.02
    ) -> float:
        """
        计算波动率调整后的仓位
        
        波动率越高，仓位越小
        """
        if entry_price <= 0 or stop_loss <= 0:
            return 0.0
        
        risk_per_unit = abs(entry_price - stop_loss) / entry_price
        
        if risk_per_unit <= 0:
            return 0.0
        
        if self.volatility.inverse_vol_weighting:
            vol_adjustment = self.volatility.base_volatility / max(current_volatility, 0.01)
            vol_adjustment = min(vol_adjustment, 2.0)
        else:
            vol_adjustment = 1.0
        
        raw_size = max_risk_pct / risk_per_unit
        adjusted_size = raw_size * vol_adjustment
        
        adjusted_size = np.clip(
            adjusted_size,
            self.volatility.min_position_pct,
            self.volatility.max_position_pct
        )
        
        return adjusted_size
    
    def calculate_drawdown_adjusted_size(self, base_size: float) -> float:
        """
        计算回撤调整后的仓位
        
        回撤越大，仓位越小
        """
        if self.current_drawdown <= self.drawdown.recovery_threshold:
            return base_size
        
        if self.current_drawdown >= self.drawdown.max_drawdown_pct:
            logger.warning(f"[PositionSizer] Max drawdown reached: {self.current_drawdown:.2%}")
            return base_size * self.drawdown.drawdown_reduction * 0.5
        
        drawdown_factor = 1.0 - (self.current_drawdown / self.drawdown.max_drawdown_pct)
        adjusted_size = base_size * drawdown_factor
        
        return adjusted_size
    
    def calculate_position_size(
        self,
        entry_price: float,
        stop_loss: float,
        side: str,
        win_rate: float = 0.5,
        avg_win: float = 0.02,
        avg_loss: float = 0.01,
        current_volatility: float = 0.5,
        confidence: float = 0.8,
        existing_positions: Optional[Dict[str, float]] = None,
        symbol: str = "",
        correlation_data: Optional[Dict[str, float]] = None
    ) -> PositionSizeResult:
        """
        综合计算最优仓位
        
        Args:
            entry_price: 入场价格
            stop_loss: 止损价格
            side: 'long' 或 'short'
            win_rate: 胜率估计
            avg_win: 平均盈利
            avg_loss: 平均亏损
            current_volatility: 当前波动率
            confidence: 信号置信度
            existing_positions: 现有持仓
            symbol: 交易品种
            correlation_data: 相关性数据
            
        Returns:
            PositionSizeResult对象
        """
        reasons = []
        warnings = []
        
        kelly_size = self.calculate_kelly_size(win_rate, avg_win, avg_loss, confidence)
        reasons.append(f"Kelly: {kelly_size:.2%}")
        
        vol_size = self.calculate_volatility_adjusted_size(
            entry_price, stop_loss, current_volatility
        )
        reasons.append(f"波动率调整: {vol_size:.2%}")
        
        base_size = min(kelly_size, vol_size)
        
        drawdown_size = self.calculate_drawdown_adjusted_size(base_size)
        if drawdown_size < base_size:
            reasons.append(f"回撤调整: {drawdown_size:.2%}")
        
        if existing_positions:
            correlated_exposure = self._calculate_correlated_exposure(
                existing_positions, symbol, correlation_data
            )
            if correlated_exposure > self.correlation.max_correlated_exposure:
                reduction_factor = self.correlation.max_correlated_exposure / (correlated_exposure + 0.01)
                drawdown_size *= reduction_factor
                warnings.append(f"相关性限制缩减: {reduction_factor:.2%}")
        
        final_size = min(drawdown_size, self.volatility.max_position_pct)
        
        if final_size < self.volatility.min_position_pct:
            warnings.append(f"最小仓位限制: {final_size:.2%} -> {self.volatility.min_position_pct:.2%}")
            final_size = self.volatility.min_position_pct
        
        risk_amount = self.total_equity * final_size * abs(entry_price - stop_loss) / entry_price
        risk_pct = risk_amount / self.total_equity if self.total_equity > 0 else 0.0
        
        leverage = 1.0 / final_size if final_size > 0 else 1.0
        
        return PositionSizeResult(
            size=self.total_equity * final_size,
            size_pct=final_size,
            risk_amount=risk_amount,
            risk_pct=risk_pct,
            leverage=leverage,
            kelly_pct=kelly_size,
            confidence=confidence,
            adjustment_reasons=reasons,
            warnings=warnings
        )
    
    def _calculate_correlated_exposure(
        self,
        existing_positions: Dict[str, float],
        current_symbol: str,
        correlation_data: Optional[Dict[str, float]] = None
    ) -> float:
        """计算相关品种的总暴露"""
        if not current_symbol:
            return 0.0
        
        total_exposure = 0.0
        
        for symbol, position_size in existing_positions.items():
            if symbol == current_symbol:
                continue
            
            if correlation_data and symbol in correlation_data:
                corr = correlation_data[symbol]
            else:
                corr = self.correlation.default_correlation.get(
                    current_symbol, {}
                ).get(symbol, 0.5)
            
            if abs(corr) >= self.correlation.correlation_threshold:
                total_exposure += abs(position_size) * abs(corr)
        
        return total_exposure
    
    def calculate_max_risk_position(
        self,
        entry_price: float,
        stop_loss: float,
        max_risk_amount: float
    ) -> float:
        """
        计算最大风险限制下的仓位
        
        Args:
            entry_price: 入场价格
            stop_loss: 止损价格
            max_risk_amount: 最大风险金额
            
        Returns:
            允许的最大仓位
        """
        risk_per_unit = abs(entry_price - stop_loss) / entry_price
        
        if risk_per_unit <= 0:
            return 0.0
        
        max_size = max_risk_amount / risk_per_unit
        
        max_size_value = self.total_equity * self.volatility.max_position_pct
        
        return min(max_size, max_size_value)
    
    def get_portfolio_allocation(
        self,
        signals: Dict[str, Dict],
        total_equity: Optional[float] = None
    ) -> Dict[str, float]:
        """
        计算组合分配
        
        Args:
            signals: 各品种信号 {symbol: {confidence, size_pct, ...}}
            total_equity: 总资金
            
        Returns:
            各品种分配金额
        """
        if total_equity:
            self.set_total_equity(total_equity)
        
        sorted_signals = sorted(
            signals.items(),
            key=lambda x: x[1].get('confidence', 0) * x[1].get('size_pct', 0),
            reverse=True
        )
        
        allocations = {}
        remaining_equity = self.total_equity
        
        for symbol, signal in sorted_signals:
            if remaining_equity <= 0:
                allocations[symbol] = 0.0
                continue
            
            size_pct = signal.get('size_pct', 0.05)
            confidence = signal.get('confidence', 0.5)
            
            adjusted_pct = size_pct * confidence
            
            allocation = remaining_equity * min(adjusted_pct, 0.3)
            
            allocations[symbol] = allocation
            remaining_equity -= allocation
        
        return allocations
    
    def get_risk_report(self) -> Dict:
        """获取风险报告"""
        total_risk = sum(
            pos.get('risk_pct', 0) 
            for pos in self.positions.values()
        )
        
        return {
            'total_equity': self.total_equity,
            'current_drawdown': self.current_drawdown,
            'total_portfolio_risk': total_risk,
            'max_single_position_risk': max(
                (pos.get('risk_pct', 0) for pos in self.positions.values()),
                default=0
            ),
            'position_count': len(self.positions),
            'kelly_config': {
                'base_kelly': self.kelly.base_kelly,
                'kelly_fraction': self.kelly.kelly_fraction,
                'max_kelly_pct': self.kelly.max_kelly_pct
            },
            'volatility_config': {
                'base_volatility': self.volatility.base_volatility,
                'max_position_pct': self.volatility.max_position_pct
            }
        }
    
    def reset(self):
        """重置状态"""
        self.current_drawdown = 0.0
        self.positions.clear()
        logger.info("[PositionSizer] State reset")


# 全局实例
_position_sizer: Optional[SmartPositionSizer] = None


def get_position_sizer() -> SmartPositionSizer:
    """获取全局仓位管理器"""
    global _position_sizer
    if _position_sizer is None:
        _position_sizer = SmartPositionSizer()
    return _position_sizer


def calculate_position(
    entry_price: float,
    stop_loss: float,
    side: str,
    **kwargs
) -> PositionSizeResult:
    """便捷函数：计算仓位"""
    sizer = get_position_sizer()
    return sizer.calculate_position_size(entry_price, stop_loss, side, **kwargs)
