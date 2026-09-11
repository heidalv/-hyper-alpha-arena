"""
Dynamic Stop Loss / Take Profit - 动态止盈止损

提供三级止盈止损系统：
1. 初始止损 (Initial Stop)
2. 移动止损 (Trailing Stop)
3. 时间止损 (Time Stop)

支持波动率调整、ATR追踪、利润保护

Author: Hyper-Alpha-Arena
"""

import logging
from typing import Dict, List, Optional, Tuple
from dataclasses import dataclass, field
from enum import Enum
import numpy as np

logger = logging.getLogger(__name__)


class StopType(Enum):
    """止损类型"""
    INITIAL = "initial"
    TRAILING = "trailing"
    BREAKEVEN = "breakeven"
    TIME = "time"


class TakeProfitLevel(Enum):
    """止盈级别"""
    LEVEL_1 = 1  # 第一目标
    LEVEL_2 = 2  # 第二目标
    LEVEL_3 = 3  # 第三目标


@dataclass
class StopLevel:
    """止损级别"""
    level: int
    price: float
    distance_pct: float
    reason: str
    active: bool = True
    triggered: bool = False


@dataclass
class TrailingStopConfig:
    """移动止损配置"""
    activation_pct: float = 0.05  # 激活移动止损的利润阈值（从 3% 提至 5%）
    trail_distance_pct: float = 0.03  # 追踪距离百分比（从 2% 提至 3%）
    trail_distance_atr: float = 2.0  # 追踪距离ATR倍数（从 1.5 提至 2.0）
    min_trail_pct: float = 0.015  # 最小追踪百分比（从 1% 提至 1.5%）
    callback_rate: float = 0.5  # 回调比例


@dataclass
class TimeStopConfig:
    """时间止损配置"""
    max_hold_periods: int = 24  # 最大持仓周期数
    max_hold_hours: float = 72.0  # 最大持仓小时数
    time_decay_start: float = 0.5  # 时间衰减开始点(持仓比例)
    warning_threshold: float = 0.8  # 警告阈值


@dataclass
class VolatilityAdjustment:
    """波动率调整"""
    base_atr_multiple: float = 2.0
    volatility_scaling: float = 1.0
    volatility_lookback: int = 14
    max_atr_multiple: float = 3.0
    min_atr_multiple: float = 1.0


@dataclass
class SLTPStrategy:
    """止盈止损策略配置"""
    use_trailing_stop: bool = True
    use_time_stop: bool = True
    use_volatility_adjustment: bool = True
    trailing_config: TrailingStopConfig = field(default_factory=TrailingStopConfig)
    time_config: TimeStopConfig = field(default_factory=TimeStopConfig)
    volatility_config: VolatilityAdjustment = field(default_factory=VolatilityAdjustment)
    
    # NOTE: 以下 TP 距离默认值仅为 fallback，实际交易中由 coordinator
    # (lev_scale + tier cap) 和 TIER_ATR_MULTIPLIER 覆盖，请勿直接依赖。
    tp1_distance_pct: float = 0.02  # 第一止盈目标距离 (fallback)
    tp2_distance_pct: float = 0.04  # 第二止盈目标距离
    tp3_distance_pct: float = 0.08  # 第三止盈目标距离
    tp1_close_pct: float = 0.33  # 第一目标平仓比例
    tp2_close_pct: float = 0.33  # 第二目标平仓比例
    tp3_close_pct: float = 0.34  # 第三目标平仓比例
    
    partial_profit_take: bool = True


class DynamicStopManager:
    """
    动态止盈止损管理器
    
    提供智能的止损和止盈管理，
    包括初始止损、移动止损、时间止损和分批止盈
    """
    
    def __init__(self, strategy: Optional[SLTPStrategy] = None):
        self.strategy = strategy or SLTPStrategy()
        self.position_states: Dict[str, Dict] = {}

    def reset_position_state(self, position_id: str) -> None:
        """2026-04-27: DCA/Pyramid 加仓后清除追踪止损内部状态。

        加仓后均价变化，旧的 trailing_high/low 和 activation_hit 已失效，
        需要重新从当前 entry 开始追踪。
        """
        self.position_states.pop(position_id, None)

    def calculate_initial_stop(
        self,
        entry_price: float,
        atr: float,
        side: str,
        volatility: Optional[float] = None,
        support_resistance: Optional[Tuple[float, float]] = None
    ) -> StopLevel:
        """
        计算初始止损
        
        Args:
            entry_price: 入场价格
            atr: ATR值
            side: 'long' 或 'short'
            volatility: 当前波动率
            support_resistance: 支撑/阻力位
            
        Returns:
            StopLevel对象
        """
        base_atr_multiple = self.strategy.volatility_config.base_atr_multiple
        
        if volatility and self.strategy.volatility_config.volatility_scaling:
            vol_adjustment = min(volatility / 0.5, 1.5)
            base_atr_multiple *= vol_adjustment
        
        base_atr_multiple = np.clip(
            base_atr_multiple,
            self.strategy.volatility_config.min_atr_multiple,
            self.strategy.volatility_config.max_atr_multiple
        )
        
        atr_stop_distance = atr * base_atr_multiple
        
        if side == 'long':
            initial_stop = entry_price - atr_stop_distance
            if support_resistance and support_resistance[0] < entry_price:
                support = support_resistance[0]
                initial_stop = min(initial_stop, support - atr * 0.5)
        else:
            initial_stop = entry_price + atr_stop_distance
            if support_resistance and support_resistance[1] > entry_price:
                resistance = support_resistance[1]
                initial_stop = max(initial_stop, resistance + atr * 0.5)
        
        if entry_price == 0:
            return StopLevel(
                level=0,
                price=0.0,
                distance_pct=0.0,
                reason="无效入场价格"
            )
        
        distance_pct = abs(entry_price - initial_stop) / entry_price
        
        return StopLevel(
            level=0,
            price=initial_stop,
            distance_pct=distance_pct,
            reason=f"初始止损: {base_atr_multiple:.1f}x ATR"
        )
    
    def calculate_take_profit_levels(
        self,
        entry_price: float,
        atr: float,
        side: str,
        strategy: Optional[SLTPStrategy] = None
    ) -> Dict[TakeProfitLevel, Tuple[float, float]]:
        """
        计算止盈级别
        
        Returns:
            止盈级别 -> (价格, 平仓比例)
        """
        s = strategy or self.strategy
        
        if side == 'long':
            tp1 = entry_price * (1 + s.tp1_distance_pct)
            tp2 = entry_price * (1 + s.tp2_distance_pct)
            tp3 = entry_price * (1 + s.tp3_distance_pct)
        else:
            tp1 = entry_price * (1 - s.tp1_distance_pct)
            tp2 = entry_price * (1 - s.tp2_distance_pct)
            tp3 = entry_price * (1 - s.tp3_distance_pct)
        
        return {
            TakeProfitLevel.LEVEL_1: (tp1, s.tp1_close_pct),
            TakeProfitLevel.LEVEL_2: (tp2, s.tp2_close_pct),
            TakeProfitLevel.LEVEL_3: (tp3, s.tp3_close_pct)
        }
    
    def calculate_trailing_stop(
        self,
        position_id: str,
        entry_price: float,
        current_price: float,
        atr: float,
        side: str,
        unrealized_pnl_pct: float,
        high_price: float,
        low_price: float,
        config: Optional[TrailingStopConfig] = None,
        tier: str = "mid",
    ) -> Tuple[float, float]:
        """
        计算移动止损（按 tier 分化 ATR 倍数）
        
        Returns:
            (止损价格, 止损类型)
        """
        cfg = config or self.strategy.trailing_config

        # tier 分化：用 TIER_ATR_MULTIPLIER 覆盖 trail_distance_atr
        try:
            from config.settings import TIER_ATR_MULTIPLIER
            tier_atr_mult = TIER_ATR_MULTIPLIER.get(tier, cfg.trail_distance_atr)
        except Exception:
            tier_atr_mult = cfg.trail_distance_atr
        
        if position_id not in self.position_states:
            self.position_states[position_id] = {
                'trailing_high': entry_price if side == 'long' else entry_price,
                'trailing_low': entry_price if side == 'short' else entry_price,
                'activation_hit': False
            }
        
        state = self.position_states[position_id]
        
        if side == 'long':
            state['trailing_high'] = max(state['trailing_high'], high_price)
            trail_from = state['trailing_high']
        else:
            state['trailing_low'] = min(state['trailing_low'], low_price)
            trail_from = state['trailing_low']

        # tier 分化激活门槛：long 仓更宽松，short 仓更紧凑
        activation = cfg.activation_pct
        if tier == "long":
            activation = max(0.03, activation * 0.7)
        elif tier == "short":
            activation = max(0.03, activation * 0.85)
        
        if not state['activation_hit'] and unrealized_pnl_pct >= activation:
            state['activation_hit'] = True
        
        if not state['activation_hit']:
            return 0.0, StopType.INITIAL
        
        if side == 'long':
            atr_trail = atr * tier_atr_mult
            pct_trail = trail_from * cfg.trail_distance_pct
            trail_distance = max(atr_trail, pct_trail, entry_price * cfg.min_trail_pct)
            stop_price = trail_from - trail_distance
        else:
            atr_trail = atr * tier_atr_mult
            pct_trail = trail_from * cfg.trail_distance_pct
            trail_distance = max(atr_trail, pct_trail, entry_price * cfg.min_trail_pct)
            stop_price = trail_from + trail_distance
        
        return stop_price, StopType.TRAILING
    
    def check_time_stop(
        self,
        position_id: str,
        hold_periods: int,
        hold_hours: float,
        entry_time: float,
        current_time: float,
        unrealized_pnl_pct: float,
        config: Optional[TimeStopConfig] = None
    ) -> Tuple[bool, str]:
        """
        检查时间止损
        
        Returns:
            (是否触发时间止损, 原因)
        """
        cfg = config or self.strategy.time_config
        
        max_periods = cfg.max_hold_periods
        max_hours = cfg.max_hold_hours
        
        if hold_periods >= max_periods or hold_hours >= max_hours:
            return True, f"超过最大持仓时间: {hold_periods}周期/{hold_hours:.1f}小时"
        
        if hold_hours >= cfg.warning_threshold * max_hours:
            logger.warning(f"[DynamicStop] 位置{position_id}接近时间止损阈值")
        
        if hold_hours >= cfg.time_decay_start * max_hours:
            time_decay_factor = (hold_hours / (cfg.max_hold_hours * 0.8))
            
            if unrealized_pnl_pct < 0 and abs(unrealized_pnl_pct) > 0.02 * time_decay_factor:
                return True, f"时间衰减触发: 持仓{hold_hours:.1f}小时, 浮亏{unrealized_pnl_pct:.2%}"
        
        return False, ""
    
    def calculate_profit_protection(
        self,
        entry_price: float,
        current_price: float,
        side: str,
        profit_protection_pct: float = 0.5
    ) -> float:
        """
        计算利润保护止损价格
        
        当盈利达到一定比例时，启动保本止损
        """
        if side == 'long':
            profit = (current_price - entry_price) / entry_price
            if profit >= profit_protection_pct:
                return entry_price
            return 0.0
        else:
            profit = (entry_price - current_price) / entry_price
            if profit >= profit_protection_pct:
                return entry_price
            return 0.0
    
    def update_position_state(
        self,
        position_id: str,
        current_price: float,
        high_price: float,
        low_price: float,
        unrealized_pnl_pct: float,
        side: str,
        atr: float
    ):
        """更新持仓状态"""
        if position_id not in self.position_states:
            self.position_states[position_id] = {
                'entry_price': 0.0,
                'side': side,
                'highest_price': high_price,
                'lowest_price': low_price,
                'trailing_activated': False,
                'tp1_hit': False,
                'tp2_hit': False
            }
        
        state = self.position_states[position_id]
        state['highest_price'] = max(state.get('highest_price', high_price), high_price)
        state['lowest_price'] = min(state.get('lowest_price', low_price), low_price)
        state['current_price'] = current_price
        state['unrealized_pnl_pct'] = unrealized_pnl_pct
        
        if unrealized_pnl_pct >= self.strategy.trailing_config.activation_pct:
            state['trailing_activated'] = True
    
    def get_sl_tp_summary(
        self,
        entry_price: float,
        current_price: float,
        side: str,
        atr: float,
        position_id: Optional[str] = None,
        unrealized_pnl_pct: float = 0.0,
        high_price: float = 0.0,
        low_price: float = 0.0
    ) -> Dict:
        """
        获取完整的止盈止损总结
        
        用于AI决策时的参数展示
        """
        initial_stop = self.calculate_initial_stop(entry_price, atr, side)
        tp_levels = self.calculate_take_profit_levels(entry_price, atr, side)
        
        trailing_stop = 0.0
        stop_type = StopType.INITIAL
        
        if position_id and self.strategy.use_trailing_stop:
            trailing_stop, stop_type = self.calculate_trailing_stop(
                position_id, entry_price, current_price, atr, side,
                unrealized_pnl_pct, high_price or current_price, low_price or current_price
            )
        
        final_stop = max(initial_stop.price, trailing_stop) if side == 'long' else min(initial_stop.price, trailing_stop)
        
        if final_stop == 0:
            final_stop = initial_stop.price
        
        profit_protection = self.calculate_profit_protection(entry_price, current_price, side)
        
        return {
            'initial_stop': {
                'price': initial_stop.price,
                'distance_pct': initial_stop.distance_pct,
                'reason': initial_stop.reason
            },
            'trailing_stop': {
                'price': trailing_stop if trailing_stop > 0 else None,
                'type': stop_type.value if trailing_stop > 0 else None
            },
            'breakeven_stop': {
                'price': profit_protection if profit_protection > 0 else None
            },
            'final_stop': final_stop,
            'take_profit_levels': {
                f'tp{level.value}': {
                    'price': price,
                    'close_pct': pct
                }
                for level, (price, pct) in tp_levels.items()
            },
            'risk_reward_ratio': {
                'tp1_rr': initial_stop.distance_pct / abs(tp_levels[TakeProfitLevel.LEVEL_1][0] - entry_price) * entry_price if tp_levels else 0,
                'tp2_rr': initial_stop.distance_pct / abs(tp_levels[TakeProfitLevel.LEVEL_2][0] - entry_price) * entry_price if tp_levels else 0,
                'tp3_rr': initial_stop.distance_pct / abs(tp_levels[TakeProfitLevel.LEVEL_3][0] - entry_price) * entry_price if tp_levels else 0
            }
        }
    
    def reset_position(self, position_id: str):
        """重置持仓状态"""
        if position_id in self.position_states:
            del self.position_states[position_id]
        logger.info(f"[DynamicStop] Position {position_id} state reset")


# 全局实例
_stop_manager: Optional[DynamicStopManager] = None


def get_stop_manager() -> DynamicStopManager:
    """获取全局止损管理器"""
    global _stop_manager
    if _stop_manager is None:
        _stop_manager = DynamicStopManager()
    return _stop_manager


def calculate_sl_tp(
    entry_price: float,
    current_price: float,
    side: str,
    atr: float,
    **kwargs
) -> Dict:
    """便捷函数：计算止盈止损"""
    manager = get_stop_manager()
    return manager.get_sl_tp_summary(entry_price, current_price, side, atr, **kwargs)
