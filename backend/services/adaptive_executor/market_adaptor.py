"""
Market Adaptor - 市场状态适配器

根据市场状态动态调整交易参数：
1. 止盈止损倍数
2. 仓位大小
3. 触发条件敏感度
4. 风险容忍度

Author: Hyper-Alpha-Arena
"""

import logging
from typing import Dict, List, Optional, Any, Tuple
from dataclasses import dataclass, field
from enum import Enum
from datetime import datetime, timedelta, timezone

logger = logging.getLogger(__name__)


class AdaptationMode(Enum):
    """适配模式"""
    CONSERVATIVE = "conservative"   # 保守模式
    STANDARD = "standard"           # 标准模式
    AGGRESSIVE = "aggressive"       # 激进模式
    AUTO = "auto"                   # 自动适配


class VolatilityRegime(Enum):
    """波动率状态"""
    LOW = "low"
    NORMAL = "normal"
    HIGH = "high"
    EXTREME = "extreme"


class LiquidityRegime(Enum):
    """流动性状态"""
    DRY = "dry"
    LOW = "low"
    NORMAL = "normal"
    HIGH = "high"


@dataclass
class MarketState:
    """市场状态"""
    symbol: str
    timestamp: datetime
    
    # 波动率
    volatility_regime: VolatilityRegime = VolatilityRegime.NORMAL
    current_atr: float = 0.0
    atr_percentile: float = 50.0  # 历史百分位
    
    # 流动性
    liquidity_regime: LiquidityRegime = LiquidityRegime.NORMAL
    spread_bps: float = 0.0
    depth_score: float = 1.0
    
    # 趋势
    trend_strength: float = 0.0  # -1 到 1
    trend_duration_hours: float = 0.0
    
    # 相关性
    btc_correlation: float = 0.0
    market_correlation: float = 0.0
    
    # 资金费率
    funding_rate: float = 0.0
    funding_pressure: str = "neutral"


@dataclass
class AdaptedParameters:
    """适配后的交易参数"""
    # 止盈止损
    stop_loss_atr_multiple: float = 2.0
    take_profit_atr_multiple: float = 3.0
    trailing_stop_activation: float = 0.02
    trailing_stop_distance: float = 0.015
    
    # 仓位
    position_size_modifier: float = 1.0
    max_position_pct: float = 0.25
    min_position_pct: float = 0.05
    
    # 入场
    entry_confidence_threshold: float = 0.6
    entry_timing_preference: str = "standard"
    
    # 风险
    max_daily_loss_pct: float = 0.05
    max_single_loss_pct: float = 0.015
    
    # 执行
    use_limit_orders: bool = True
    slippage_tolerance_pct: float = 0.002
    
    # 原因
    adaptation_reasons: List[str] = field(default_factory=list)


@dataclass
class AdaptationConfig:
    """适配配置"""
    # 波动率阈值
    volatility_low_percentile: float = 25.0
    volatility_high_percentile: float = 75.0
    volatility_extreme_percentile: float = 95.0
    
    # 流动性阈值
    spread_high_bps: float = 10.0
    spread_extreme_bps: float = 20.0
    depth_low_threshold: float = 0.5
    
    # 趋势阈值
    strong_trend_threshold: float = 0.7
    weak_trend_threshold: float = 0.3
    
    # 资金费率阈值
    funding_high_threshold: float = 0.01
    funding_extreme_threshold: float = 0.03


class MarketAdaptor:
    """
    市场状态适配器
    
    根据当前市场状态动态调整交易参数，
    以适应不同的市场环境
    """
    
    def __init__(
        self,
        config: Optional[AdaptationConfig] = None,
        mode: AdaptationMode = AdaptationMode.AUTO
    ):
        self.config = config or AdaptationConfig()
        self.mode = mode
        
        # 市场状态缓存
        self._market_states: Dict[str, MarketState] = {}
        
        # 参数历史
        self._parameter_history: Dict[str, List[AdaptedParameters]] = {}
        
        # 基准参数
        self._base_parameters = AdaptedParameters()
        
        logger.info(f"[MarketAdaptor] Initialized with mode: {mode.value}")
    
    def assess_market_state(
        self,
        symbol: str,
        current_price: float,
        atr: float,
        atr_history: Optional[List[float]] = None,
        spread: float = 0.0,
        depth_score: float = 1.0,
        trend_indicator: float = 0.0,
        funding_rate: float = 0.0
    ) -> MarketState:
        """
        评估市场状态
        
        Args:
            symbol: 交易品种
            current_price: 当前价格
            atr: 当前ATR
            atr_history: ATR历史数据
            spread: 买卖价差
            depth_score: 深度评分
            trend_indicator: 趋势指标
            funding_rate: 资金费率
            
        Returns:
            MarketState 市场状态
        """
        state = MarketState(
            symbol=symbol,
            timestamp=datetime.now(timezone.utc),
            current_atr=atr
        )
        
        # 评估波动率状态
        state.volatility_regime = self._assess_volatility(atr, atr_history, current_price)
        if atr_history:
            state.atr_percentile = self._calculate_percentile(atr, atr_history)
        
        # 评估流动性状态
        state.liquidity_regime = self._assess_liquidity(spread, depth_score)
        state.spread_bps = spread * 10000  # 转换为基点
        state.depth_score = depth_score
        
        # 评估趋势
        state.trend_strength = trend_indicator
        
        # 评估资金费率压力
        state.funding_rate = funding_rate
        state.funding_pressure = self._assess_funding_pressure(funding_rate)
        
        # 缓存状态
        self._market_states[symbol] = state
        
        return state
    
    def adapt_parameters(
        self,
        symbol: str,
        market_state: Optional[MarketState] = None,
        base_params: Optional[AdaptedParameters] = None
    ) -> AdaptedParameters:
        """
        根据市场状态适配交易参数
        
        Args:
            symbol: 交易品种
            market_state: 市场状态 (可选，会使用缓存)
            base_params: 基准参数 (可选)
            
        Returns:
            AdaptedParameters 适配后的参数
        """
        if market_state is None:
            market_state = self._market_states.get(symbol)
        
        if market_state is None:
            logger.warning(f"[MarketAdaptor] No market state for {symbol}, using base parameters")
            return base_params or self._base_parameters
        
        params = AdaptedParameters()
        reasons = []
        
        # ========== 波动率适配 ==========
        vol_regime = market_state.volatility_regime
        
        if vol_regime == VolatilityRegime.LOW:
            # 低波动：收紧止损，增大仓位
            params.stop_loss_atr_multiple = 1.5
            params.take_profit_atr_multiple = 2.0
            params.position_size_modifier = 1.2
            params.trailing_stop_distance = 0.01
            reasons.append("低波动环境: 收紧止损, 适度增仓")
        
        elif vol_regime == VolatilityRegime.HIGH:
            # 高波动：放宽止损，减小仓位
            params.stop_loss_atr_multiple = 2.5
            params.take_profit_atr_multiple = 4.0
            params.position_size_modifier = 0.7
            params.trailing_stop_distance = 0.025
            reasons.append("高波动环境: 放宽止损, 减小仓位")
        
        elif vol_regime == VolatilityRegime.EXTREME:
            # 极端波动：大幅放宽止损，显著减仓
            params.stop_loss_atr_multiple = 3.0
            params.take_profit_atr_multiple = 5.0
            params.position_size_modifier = 0.4
            params.max_position_pct = 0.15
            params.trailing_stop_distance = 0.04
            reasons.append("极端波动环境: 大幅放宽止损, 显著减仓")
        
        # ========== 流动性适配 ==========
        liq_regime = market_state.liquidity_regime
        
        if liq_regime == LiquidityRegime.LOW:
            params.use_limit_orders = True
            params.slippage_tolerance_pct = 0.003
            params.position_size_modifier *= 0.8
            reasons.append("低流动性: 使用限价单, 减小仓位")
        
        elif liq_regime == LiquidityRegime.DRY:
            params.use_limit_orders = True
            params.slippage_tolerance_pct = 0.005
            params.position_size_modifier *= 0.5
            params.max_position_pct = 0.1
            reasons.append("流动性枯竭: 限价单优先, 大幅减仓")
        
        # ========== 趋势适配 ==========
        trend = market_state.trend_strength
        
        if abs(trend) > self.config.strong_trend_threshold:
            # 强趋势：顺势加仓
            params.position_size_modifier *= 1.2
            params.take_profit_atr_multiple *= 1.3
            params.entry_timing_preference = "aggressive"
            direction = "多头" if trend > 0 else "空头"
            reasons.append(f"强{direction}趋势: 增加仓位, 扩大止盈")
        
        elif abs(trend) < self.config.weak_trend_threshold:
            # 弱趋势/震荡：减仓
            params.position_size_modifier *= 0.7
            params.entry_confidence_threshold = 0.7
            reasons.append("震荡市场: 减小仓位, 提高入场门槛")
        
        # ========== 资金费率适配 ==========
        funding = market_state.funding_rate
        
        if abs(funding) > self.config.funding_extreme_threshold:
            # 极端资金费率
            params.position_size_modifier *= 0.6
            params.max_daily_loss_pct = 0.03
            direction = "做空" if funding > 0 else "做多"
            reasons.append(f"极端资金费率: 建议{direction}, 减小仓位")
        
        elif abs(funding) > self.config.funding_high_threshold:
            params.position_size_modifier *= 0.8
            reasons.append("高资金费率: 适度减仓")
        
        # ========== 模式调整 ==========
        if self.mode == AdaptationMode.CONSERVATIVE:
            params.position_size_modifier *= 0.7
            params.entry_confidence_threshold = 0.75
            params.max_daily_loss_pct = 0.03
            reasons.append("保守模式: 整体减仓")
        
        elif self.mode == AdaptationMode.AGGRESSIVE:
            params.position_size_modifier *= 1.3
            params.entry_confidence_threshold = 0.5
            params.max_daily_loss_pct = 0.08
            reasons.append("激进模式: 整体加仓")
        
        # 确保仓位在合理范围内
        params.position_size_modifier = max(0.2, min(2.0, params.position_size_modifier))
        
        params.adaptation_reasons = reasons
        
        # 记录历史
        if symbol not in self._parameter_history:
            self._parameter_history[symbol] = []
        self._parameter_history[symbol].append(params)
        
        return params
    
    def _assess_volatility(
        self,
        current_atr: float,
        atr_history: Optional[List[float]],
        price: float
    ) -> VolatilityRegime:
        """评估波动率状态"""
        if atr_history is None or len(atr_history) < 10:
            atr_pct = current_atr / price if price > 0 else 0
            if atr_pct < 0.015:
                return VolatilityRegime.LOW
            elif atr_pct > 0.05:
                return VolatilityRegime.EXTREME
            elif atr_pct > 0.03:
                return VolatilityRegime.HIGH
            return VolatilityRegime.NORMAL
        
        percentile = self._calculate_percentile(current_atr, atr_history)
        
        if percentile < self.config.volatility_low_percentile:
            return VolatilityRegime.LOW
        elif percentile > self.config.volatility_extreme_percentile:
            return VolatilityRegime.EXTREME
        elif percentile > self.config.volatility_high_percentile:
            return VolatilityRegime.HIGH
        return VolatilityRegime.NORMAL
    
    def _assess_liquidity(self, spread: float, depth_score: float) -> LiquidityRegime:
        """评估流动性状态"""
        spread_bps = spread * 10000
        
        if spread_bps > self.config.spread_extreme_bps or depth_score < 0.3:
            return LiquidityRegime.DRY
        elif spread_bps > self.config.spread_high_bps or depth_score < self.config.depth_low_threshold:
            return LiquidityRegime.LOW
        elif spread_bps < 3 and depth_score > 1.2:
            return LiquidityRegime.HIGH
        return LiquidityRegime.NORMAL
    
    def _assess_funding_pressure(self, funding_rate: float) -> str:
        """评估资金费率压力"""
        if funding_rate > self.config.funding_extreme_threshold:
            return "extreme_long"
        elif funding_rate > self.config.funding_high_threshold:
            return "high_long"
        elif funding_rate < -self.config.funding_extreme_threshold:
            return "extreme_short"
        elif funding_rate < -self.config.funding_high_threshold:
            return "high_short"
        return "neutral"
    
    def _calculate_percentile(self, value: float, history: List[float]) -> float:
        """计算百分位"""
        if not history:
            return 50.0
        count_below = sum(1 for h in history if h < value)
        return (count_below / len(history)) * 100
    
    def get_market_state(self, symbol: str) -> Optional[MarketState]:
        """获取市场状态"""
        return self._market_states.get(symbol)
    
    def get_parameter_history(self, symbol: str, limit: int = 20) -> List[AdaptedParameters]:
        """获取参数历史"""
        history = self._parameter_history.get(symbol, [])
        return history[-limit:]
    
    def set_mode(self, mode: AdaptationMode):
        """设置适配模式"""
        self.mode = mode
        logger.info(f"[MarketAdaptor] Mode changed to: {mode.value}")
    
    def get_status(self) -> Dict[str, Any]:
        """获取状态"""
        return {
            "mode": self.mode.value,
            "tracked_symbols": len(self._market_states),
            "states": {
                symbol: {
                    "volatility": state.volatility_regime.value,
                    "liquidity": state.liquidity_regime.value,
                    "trend": state.trend_strength,
                    "funding": state.funding_rate
                }
                for symbol, state in self._market_states.items()
            }
        }


# 全局实例
_market_adaptor: Optional[MarketAdaptor] = None


def get_market_adaptor() -> MarketAdaptor:
    """获取全局市场适配器"""
    global _market_adaptor
    if _market_adaptor is None:
        _market_adaptor = MarketAdaptor()
    return _market_adaptor
