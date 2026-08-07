"""
Dynamic Risk Allocator - 动态风险分配器

根据市场状态和策略表现动态分配风险预算：
1. 基于波动率调整仓位
2. 基于相关性调整敞口
3. 基于近期表现调整风险
4. 实时风险监控与调整
"""

import logging
from typing import Dict, Optional, List, Tuple
from dataclasses import dataclass, field
from datetime import datetime, timezone
import numpy as np
from collections import defaultdict

logger = logging.getLogger(__name__)


@dataclass
class RiskAllocation:
    """风险分配结果"""
    symbol: str
    base_position_pct: float = 0.0      # 基础仓位
    volatility_adjustment: float = 1.0  # 波动率调整因子
    correlation_adjustment: float = 1.0 # 相关性调整因子
    performance_adjustment: float = 1.0 # 表现调整因子
    final_position_pct: float = 0.0     # 最终仓位
    risk_score: float = 0.0             # 风险评分 (0-1)
    stop_loss_pct: float = 0.02         # 建议止损比例
    take_profit_pct: float = 0.06       # 建议止盈比例


@dataclass
class PortfolioRiskState:
    """组合风险状态"""
    total_exposure: float = 0.0         # 总敞口
    max_single_exposure: float = 0.0    # 最大单品种敞口
    correlation_risk: float = 0.0       # 相关性风险
    volatility_risk: float = 0.0        # 波动率风险
    concentration_risk: float = 0.0     # 集中度风险
    overall_risk_score: float = 0.0     # 整体风险评分
    is_overallocated: bool = False      # 是否超配
    requires_rebalancing: bool = False  # 是否需要再平衡


class RiskAllocator:
    """
    动态风险分配器
    
    根据多维度因素动态调整各品种的风险分配：
    - 波动率调整：高波动降低仓位
    - 相关性调整：高相关降低总体敞口
    - 表现调整：近期表现好增加仓位
    - 实时监控：动态调整风险参数
    """
    
    # 默认配置
    DEFAULT_MAX_SINGLE_EXPOSURE = 0.30      # 单品种最大30%
    DEFAULT_MAX_TOTAL_EXPOSURE = 2.0        # 总杠杆最大2倍
    DEFAULT_VOLATILITY_THRESHOLD = 0.02     # 波动率阈值2%
    DEFAULT_CORRELATION_THRESHOLD = 0.7     # 相关性阈值0.7
    
    def __init__(
        self,
        max_single_exposure: float = None,
        max_total_exposure: float = None,
        volatility_threshold: float = None
    ):
        self.max_single_exposure = max_single_exposure or self.DEFAULT_MAX_SINGLE_EXPOSURE
        self.max_total_exposure = max_total_exposure or self.DEFAULT_MAX_TOTAL_EXPOSURE
        self.volatility_threshold = volatility_threshold or self.DEFAULT_VOLATILITY_THRESHOLD
        
        # 持仓状态
        self.current_positions: Dict[str, Dict] = {}
        
        # 相关性矩阵缓存
        self.correlation_matrix: Dict[str, Dict[str, float]] = {}
        
        # 绩效历史
        self.performance_history: Dict[str, List[float]] = defaultdict(list)
        
    def allocate_risk(
        self,
        symbol: str,
        signal_strength: float,          # AI信号强度 0-1
        volatility: float,                # 当前波动率
        correlation_with_portfolio: float, # 与组合相关性
        recent_returns: List[float],      # 近期收益
        account_balance: float,
        current_position: float = 0.0     # 当前持仓
    ) -> RiskAllocation:
        """
        计算单品种的风险分配
        
        Args:
            symbol: 交易品种
            signal_strength: AI信号强度 (0-1)
            volatility: 当前波动率
            correlation_with_portfolio: 与组合相关性
            recent_returns: 近期收益列表
            account_balance: 账户余额
            current_position: 当前持仓
            
        Returns:
            风险分配结果
        """
        allocation = RiskAllocation(symbol=symbol)
        
        # 1. 基础仓位（基于信号强度）
        base_position = signal_strength * 0.30  # 最大30%
        allocation.base_position_pct = base_position
        
        # 2. 波动率调整
        vol_adjustment = self._calculate_volatility_adjustment(volatility)
        allocation.volatility_adjustment = vol_adjustment
        
        # 3. 相关性调整
        corr_adjustment = self._calculate_correlation_adjustment(
            symbol, correlation_with_portfolio
        )
        allocation.correlation_adjustment = corr_adjustment
        
        # 4. 表现调整
        perf_adjustment = self._calculate_performance_adjustment(
            symbol, recent_returns
        )
        allocation.performance_adjustment = perf_adjustment
        
        # 5. 计算最终仓位
        final_position = (
            base_position * 
            vol_adjustment * 
            corr_adjustment * 
            perf_adjustment
        )
        
        # 6. 限制范围
        final_position = max(0.01, min(self.max_single_exposure, final_position))
        allocation.final_position_pct = final_position
        
        # 7. 计算风险评分
        allocation.risk_score = self._calculate_risk_score(
            final_position, volatility, correlation_with_portfolio
        )
        
        # 8. 设置止损止盈
        allocation.stop_loss_pct = self._calculate_stop_loss(volatility)
        allocation.take_profit_pct = self._calculate_take_profit(
            volatility, signal_strength
        )
        
        # 更新状态
        self._update_position_state(symbol, final_position, volatility)
        
        logger.debug(f"[RiskAllocator] {symbol}: base={base_position:.2%}, "
                    f"final={final_position:.2%}, risk={allocation.risk_score:.2f}")
        
        return allocation
    
    def _calculate_volatility_adjustment(self, volatility: float) -> float:
        """计算波动率调整因子"""
        if volatility < self.volatility_threshold:
            return 1.0  # 正常波动
            
        # 波动率越高，仓位越低
        ratio = volatility / self.volatility_threshold
        adjustment = 1.0 / (1 + (ratio - 1) * 0.5)
        
        return max(0.3, min(1.0, adjustment))
    
    def _calculate_correlation_adjustment(
        self, 
        symbol: str, 
        correlation: float
    ) -> float:
        """计算相关性调整因子"""
        if correlation < 0.3:
            return 1.0  # 低相关，正常仓位
            
        if correlation > 0.8:
            return 0.5  # 高相关，降低仓位
            
        # 中等相关性
        return 0.7
    
    def _calculate_performance_adjustment(
        self, 
        symbol: str, 
        returns: List[float]
    ) -> float:
        """计算表现调整因子"""
        if not returns:
            return 1.0
            
        avg_return = np.mean(returns)
        win_rate = np.mean([1 if r > 0 else 0 for r in returns])
        
        # 近期表现好，增加仓位
        if avg_return > 0.02 and win_rate > 0.6:
            return 1.2
            
        # 近期表现差，减少仓位
        elif avg_return < -0.02 or win_rate < 0.4:
            return 0.7
            
        return 1.0
    
    def _calculate_risk_score(
        self,
        position_pct: float,
        volatility: float,
        correlation: float
    ) -> float:
        """计算综合风险评分 (0-1)"""
        score = 0.0
        
        # 仓位风险权重 40%
        score += (position_pct / self.max_single_exposure) * 0.40
        
        # 波动率风险权重 30%
        score += min(1.0, volatility / 0.05) * 0.30
        
        # 相关性风险权重 30%
        score += correlation * 0.30
        
        return min(1.0, score)
    
    def _calculate_stop_loss(self, volatility: float) -> float:
        """计算建议止损比例"""
        # 止损至少1.5倍ATR
        base_sl = 0.015  # 1.5%
        sl = base_sl * (1 + volatility * 10)
        return min(0.05, max(0.01, sl))  # 1%-5%
    
    def _calculate_take_profit(
        self, 
        volatility: float, 
        signal_strength: float
    ) -> float:
        """计算建议止盈比例"""
        base_tp = 0.06  # 6%
        tp = base_tp * (1 + signal_strength * 0.5) * (1 + volatility * 5)
        return min(0.15, max(0.03, tp))  # 3%-15%
    
    def _update_position_state(
        self, 
        symbol: str, 
        position: float, 
        volatility: float
    ):
        """更新持仓状态"""
        self.current_positions[symbol] = {
            'position': position,
            'volatility': volatility,
            'updated_at': datetime.now(timezone.utc)
        }
    
    def get_portfolio_risk_state(self) -> PortfolioRiskState:
        """获取组合风险状态"""
        state = PortfolioRiskState()
        
        if not self.current_positions:
            return state
            
        positions = list(self.current_positions.values())
        
        # 总敞口
        state.total_exposure = sum(p['position'] for p in positions)
        
        # 最大单品种敞口
        state.max_single_exposure = max(p['position'] for p in positions)
        
        # 集中度风险
        state.concentration_risk = state.max_single_exposure / (state.total_exposure + 1e-8)
        
        # 整体风险评分
        state.overall_risk_score = np.mean([
            p['position'] / self.max_single_exposure * 0.5 +
            p['volatility'] / 0.05 * 0.5
            for p in positions
        ])
        
        # 检查是否超配
        state.is_overallocated = (
            state.total_exposure > self.max_total_exposure or
            state.max_single_exposure > self.max_single_exposure
        )
        
        # 是否需要再平衡
        state.requires_rebalancing = (
            state.is_overallocated or
            state.concentration_risk > 0.5
        )
        
        return state
    
    def rebalance_portfolio(
        self,
        allocations: List[RiskAllocation]
    ) -> List[RiskAllocation]:
        """
        重新平衡组合
        
        Args:
            allocations: 当前风险分配列表
            
        Returns:
            调整后的风险分配列表
        """
        if not allocations:
            return []
            
        # 计算当前总敞口
        current_total = sum(a.final_position_pct for a in allocations)
        
        if current_total <= 0:
            return allocations
            
        # 目标总敞口
        target_total = min(
            self.max_total_exposure,
            current_total
        )
        
        # 调整各品种仓位
        adjusted = []
        for alloc in allocations:
            # 按比例缩放
            ratio = target_total / current_total if current_total > 0 else 1.0
            new_position = alloc.final_position_pct * ratio
            
            # 限制单品种最大
            new_position = min(self.max_single_exposure, new_position)
            
            alloc.final_position_pct = new_position
            alloc.risk_score = self._calculate_risk_score(
                new_position,
                alloc.volatility_adjustment,
                alloc.correlation_adjustment
            )
            adjusted.append(alloc)
            
        logger.info(f"[RiskAllocator] Rebalanced {len(adjusted)} positions, "
                   f"total exposure: {target_total:.2%}")
        
        return adjusted
    
    def update_correlation_matrix(
        self, 
        returns_data: Dict[str, List[float]]
    ):
        """更新相关性矩阵"""
        symbols = list(returns_data.keys())
        
        for s1 in symbols:
            self.correlation_matrix[s1] = {}
            for s2 in symbols:
                if s1 == s2:
                    self.correlation_matrix[s1][s2] = 1.0
                else:
                    corr = self._calculate_correlation(
                        returns_data[s1], 
                        returns_data[s2]
                    )
                    self.correlation_matrix[s1][s2] = corr
    
    def _calculate_correlation(
        self, 
        returns1: List[float], 
        returns2: List[float]
    ) -> float:
        """计算相关性"""
        if len(returns1) < 5 or len(returns2) < 5:
            return 0.0
            
        min_len = min(len(returns1), len(returns2))
        r1 = returns1[-min_len:]
        r2 = returns2[-min_len:]
        
        if np.std(r1) < 1e-8 or np.std(r2) < 1e-8:
            return 0.0
            
        corr = np.corrcoef(r1, r2)[0, 1]
        return float(corr) if not np.isnan(corr) else 0.0


class DynamicRiskAllocator(RiskAllocator):
    """
    动态风险分配器增强版
    
    额外的动态调整功能：
    - 基于回撤的动态降仓
    - 基于盈利的保护性减仓
    - 实时波动率监控
    """
    
    def __init__(
        self,
        max_single_exposure: float = None,
        max_total_exposure: float = None,
        volatility_threshold: float = None,
        drawdown_protection: bool = True
    ):
        super().__init__(max_single_exposure, max_total_exposure, volatility_threshold)
        self.drawdown_protection = drawdown_protection
        
        # 回撤记录
        self.equity_history: List[float] = []
        self.peak_equity: float = 0.0
        self.current_drawdown: float = 0.0
        
    def update_equity(self, equity: float):
        """更新权益，计算回撤"""
        self.equity_history.append({
            'timestamp': datetime.now(timezone.utc),
            'equity': equity
        })
        
        # 更新峰值
        if equity > self.peak_equity:
            self.peak_equity = equity
            
        # 计算回撤
        if self.peak_equity > 0:
            self.current_drawdown = (self.peak_equity - equity) / self.peak_equity
            
    def get_drawdown_adjustment(self) -> float:
        """获取回撤调整因子"""
        if not self.drawdown_protection:
            return 1.0
            
        if self.current_drawdown < 0.05:
            return 1.0
        elif self.current_drawdown < 0.10:
            return 0.8
        elif self.current_drawdown < 0.15:
            return 0.5
        else:
            return 0.3  # 严重回撤，大幅降仓
    
    def allocate_with_protection(
        self,
        symbol: str,
        signal_strength: float,
        volatility: float,
        correlation_with_portfolio: float,
        recent_returns: List[float],
        account_balance: float,
        current_position: float = 0.0
    ) -> RiskAllocation:
        """带保护机制的风险分配"""
        # 基础分配
        allocation = self.allocate_risk(
            symbol, signal_strength, volatility,
            correlation_with_portfolio, recent_returns,
            account_balance, current_position
        )
        
        # 应用回撤保护
        dd_adjustment = self.get_drawdown_adjustment()
        allocation.final_position_pct *= dd_adjustment
        allocation.performance_adjustment *= dd_adjustment
        
        logger.debug(f"[DynamicRiskAllocator] {symbol}: "
                    f"drawdown={self.current_drawdown:.1%}, "
                    f"adjustment={dd_adjustment:.1%}")
        
        return allocation


# 全局实例
risk_allocator = RiskAllocator()
dynamic_risk_allocator = DynamicRiskAllocator()
