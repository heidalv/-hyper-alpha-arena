"""
ATAS V2 仓位管理器

动态仓位计算，支持多种sizing方法
"""
from dataclasses import dataclass
from enum import Enum
from typing import Dict, Optional
import numpy as np


class PositionSizingMethod(Enum):
    """仓位计算方法"""
    FIXED_AMOUNT = "fixed_amount"  # 固定金额
    FIXED_RATIO = "fixed_ratio"  # 固定比例
    KELLY = "kelly"  # 凯利公式
    ATR_BASED = "atr_based"  # 基于ATR
    VOLATILITY_ADJUSTED = "volatility_adjusted"  # 波动率调整
    RISK_PARITY = "risk_parity"  # 风险平价


@dataclass
class PositionSizeResult:
    """仓位计算结果"""
    quantity: float  # 数量
    value: float  # 金额
    risk_amount: float  # 风险金额
    stop_loss_price: Optional[float] = None  # 止损价格


class PositionManager:
    """仓位管理器"""
    
    def __init__(self, default_method: PositionSizingMethod = PositionSizingMethod.FIXED_RATIO):
        self.default_method = default_method
    
    def calculate(
        self,
        method: Optional[PositionSizingMethod],
        account_value: float,
        entry_price: float,
        stop_loss_price: Optional[float] = None,
        **kwargs
    ) -> PositionSizeResult:
        """
        计算仓位大小
        
        Args:
            method: 计算方法
            account_value: 账户价值
            entry_price: 入场价格
            stop_loss_price: 止损价格
            **kwargs: 其他参数
            
        Returns:
            PositionSizeResult: 仓位结果
        """
        method = method or self.default_method
        
        if method == PositionSizingMethod.FIXED_AMOUNT:
            return self._fixed_amount(account_value, entry_price, **kwargs)
        elif method == PositionSizingMethod.FIXED_RATIO:
            return self._fixed_ratio(account_value, entry_price, **kwargs)
        elif method == PositionSizingMethod.KELLY:
            return self._kelly(account_value, entry_price, **kwargs)
        elif method == PositionSizingMethod.ATR_BASED:
            return self._atr_based(account_value, entry_price, stop_loss_price, **kwargs)
        elif method == PositionSizingMethod.VOLATILITY_ADJUSTED:
            return self._volatility_adjusted(account_value, entry_price, **kwargs)
        elif method == PositionSizingMethod.RISK_PARITY:
            return self._risk_parity(account_value, entry_price, **kwargs)
        else:
            raise ValueError(f"Unknown position sizing method: {method}")
    
    def _fixed_amount(self, account_value: float, entry_price: float, **kwargs) -> PositionSizeResult:
        """固定金额法"""
        amount = kwargs.get('amount', 1000)
        quantity = amount / entry_price
        
        return PositionSizeResult(
            quantity=quantity,
            value=amount,
            risk_amount=amount * 0.02  # 假设2%风险
        )
    
    def _fixed_ratio(self, account_value: float, entry_price: float, **kwargs) -> PositionSizeResult:
        """固定比例法"""
        ratio = kwargs.get('ratio', 0.1)  # 默认10%
        value = account_value * ratio
        quantity = value / entry_price
        
        return PositionSizeResult(
            quantity=quantity,
            value=value,
            risk_amount=value * 0.02
        )
    
    def _kelly(self, account_value: float, entry_price: float, **kwargs) -> PositionSizeResult:
        """凯利公式法"""
        win_rate = kwargs.get('win_rate', 0.55)
        avg_win = kwargs.get('avg_win', 1.0)
        avg_loss = kwargs.get('avg_loss', -1.0)
        
        # Kelly百分比 = (p*b - q) / b
        # p=胜率, q=败率, b=盈亏比
        win_loss_ratio = abs(avg_win / avg_loss) if avg_loss != 0 else 1.5
        kelly_pct = (win_rate * win_loss_ratio - (1 - win_rate)) / win_loss_ratio
        
        # 保守起见，使用半凯利
        kelly_pct = max(0, min(kelly_pct * 0.5, 0.25))
        
        value = account_value * kelly_pct
        quantity = value / entry_price
        
        return PositionSizeResult(
            quantity=quantity,
            value=value,
            risk_amount=value * 0.02
        )
    
    def _atr_based(
        self,
        account_value: float,
        entry_price: float,
        stop_loss_price: Optional[float],
        **kwargs
    ) -> PositionSizeResult:
        """基于ATR的仓位管理"""
        atr = kwargs.get('atr', entry_price * 0.02)
        risk_per_trade = kwargs.get('risk_per_trade', 0.01)  # 单笔风险1%
        atr_multiplier = kwargs.get('atr_multiplier', 2.0)
        
        # 计算止损距离
        if stop_loss_price:
            stop_distance = abs(entry_price - stop_loss_price)
        else:
            stop_distance = atr * atr_multiplier
        
        # 计算风险金额
        risk_amount = account_value * risk_per_trade
        
        # 计算仓位数量
        quantity = risk_amount / stop_distance
        value = quantity * entry_price
        
        return PositionSizeResult(
            quantity=quantity,
            value=value,
            risk_amount=risk_amount,
            stop_loss_price=entry_price - stop_distance if not stop_loss_price else stop_loss_price
        )
    
    def _volatility_adjusted(self, account_value: float, entry_price: float, **kwargs) -> PositionSizeResult:
        """波动率调整法"""
        volatility = kwargs.get('volatility', 0.02)
        target_volatility = kwargs.get('target_volatility', 0.15)
        base_ratio = kwargs.get('base_ratio', 0.1)
        
        # 根据波动率调整仓位
        vol_adjustment = target_volatility / (volatility * np.sqrt(252))
        adjusted_ratio = base_ratio * vol_adjustment
        adjusted_ratio = max(0.01, min(adjusted_ratio, 0.3))  # 限制在1%-30%
        
        value = account_value * adjusted_ratio
        quantity = value / entry_price
        
        return PositionSizeResult(
            quantity=quantity,
            value=value,
            risk_amount=value * volatility
        )
    
    def _risk_parity(self, account_value: float, entry_price: float, **kwargs) -> PositionSizeResult:
        """风险平价法"""
        asset_volatility = kwargs.get('volatility', 0.02)
        target_risk = kwargs.get('target_risk', 0.10)
        
        # 计算使资产贡献目标风险所需的权重
        weight = target_risk / (asset_volatility * np.sqrt(252))
        weight = max(0.01, min(weight, 0.5))
        
        value = account_value * weight
        quantity = value / entry_price
        
        return PositionSizeResult(
            quantity=quantity,
            value=value,
            risk_amount=value * asset_volatility
        )


def calculate_position_size(
    account_value: float,
    entry_price: float,
    method: PositionSizingMethod = PositionSizingMethod.FIXED_RATIO,
    stop_loss_price: Optional[float] = None,
    **kwargs
) -> PositionSizeResult:
    """便捷函数：计算仓位"""
    manager = PositionManager()
    return manager.calculate(method, account_value, entry_price, stop_loss_price, **kwargs)
