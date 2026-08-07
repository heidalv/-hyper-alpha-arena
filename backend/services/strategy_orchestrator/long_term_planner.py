"""
Long-Term Planner - 中长期规划器

负责宏观周期判断和大方向决策：
1. 市场周期分析（牛市/熊市/震荡）
2. 趋势强度评估
3. 风险预算分配
4. 策略方向指引
"""

import logging
from typing import Dict, Optional, Tuple, List
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from enum import Enum
import pandas as pd
import numpy as np

logger = logging.getLogger(__name__)


class MarketCycle(Enum):
    """市场周期"""
    BULL_TREND = "bull_trend"        # 上涨趋势
    BEAR_TREND = "bear_trend"        # 下跌趋势
    HIGH_VOLATILITY = "high_volatility"   # 高波动
    LOW_VOLATILITY = "low_volatility"     # 低波动
    ACCUMULATION = "accumulation"    # 吸筹阶段
    DISTRIBUTION = "distribution"    # 派发阶段
    UNKNOWN = "unknown"


@dataclass
class CycleIndicators:
    """周期指标"""
    sma_200_position: float = 0.0       # 价格在200日均线上方(1)或下方(0)
    sma_50_200_cross: float = 0.0       # 金叉(1)/死叉(-1)/无信号(0)
    atr_percentile: float = 0.5         # ATR百分位 (0-1)
    volume_trend: float = 0.0           # 成交量趋势
    oi_trend: float = 0.0               # OI变化趋势
    funding_rate_avg: float = 0.0       # 平均资金费率
    market_dominance: float = 0.5       # BTC市值占比


@dataclass
class RiskBudget:
    """风险预算配置"""
    max_daily_loss_pct: float = 0.05    # 日最大亏损5%
    max_position_size: float = 0.30     # 单笔最大30%
    max_correlation_exposure: float = 0.50  # 相关性敞口50%
    min_cash_reserve: float = 0.10      # 最低10%现金储备
    trend_direction: int = 0            # 1=做多, -1=做空, 0=中性


@dataclass
class PlanningResult:
    """规划结果"""
    market_cycle: MarketCycle = MarketCycle.UNKNOWN
    cycle_confidence: float = 0.0       # 周期判断置信度
    risk_budget: RiskBudget = field(default_factory=RiskBudget)
    recommended_leverage: float = 10.0  # 建议杠杆（默认10x）
    position_bias: str = "neutral"      # neutral/long/short
    key_levels: Dict[str, float] = field(default_factory=dict)  # 关键支撑/阻力
    regime_transition_warning: bool = False  # 状态转换警告
    planning_timestamp: datetime = field(default_factory=datetime.utcnow)
    # 战略分析师增强字段
    macro_regime: str = "unknown"       # 宏观体制 (risk_on/risk_off/neutral/transition)
    strategic_context: Optional[Dict] = None  # 战略上下文摘要


class LongTermPlanner:
    """
    中长期规划器
    
    基于宏观指标进行市场周期判断，
    输出风险预算和策略方向指引
    """
    
    # 周期判断阈值
    BULL_THRESHOLD = 0.6
    BEAR_THRESHOLD = -0.6
    HIGH_VOL_THRESHOLD = 0.7
    LOW_VOL_THRESHOLD = 0.3
    
    def __init__(self):
        self.cycle_history: List[Dict] = []
        
    def analyze_market_cycle(
        self, 
        klines: pd.DataFrame,
        funding_history: List[float],
        volume_data: pd.DataFrame,
        oi_data: Optional[pd.DataFrame] = None
    ) -> Tuple[MarketCycle, float, CycleIndicators]:
        """
        分析市场周期
        
        Args:
            klines: K线数据
            funding_history: 资金费率历史
            volume_data: 成交量数据
            oi_data: OI数据（可选）
            
        Returns:
            (周期类型, 置信度, 周期指标)
        """
        indicators = self._calculate_indicators(
            klines, funding_history, volume_data, oi_data
        )
        
        # 综合评分
        cycle_score = self._calculate_cycle_score(indicators)
        
        # 判断周期
        if cycle_score > self.BULL_THRESHOLD:
            cycle = MarketCycle.BULL_TREND
        elif cycle_score < self.BEAR_THRESHOLD:
            cycle = MarketCycle.BEAR_TREND
        elif indicators.atr_percentile > self.HIGH_VOL_THRESHOLD:
            cycle = MarketCycle.HIGH_VOLATILITY
        elif indicators.atr_percentile < self.LOW_VOL_THRESHOLD:
            cycle = MarketCycle.LOW_VOLATILITY
        else:
            cycle = MarketCycle.ACCUMULATION
            
        confidence = abs(cycle_score)
        
        return cycle, confidence, indicators
    
    def _calculate_indicators(
        self,
        klines: pd.DataFrame,
        funding_history: List[float],
        volume_data: pd.DataFrame,
        oi_data: Optional[pd.DataFrame] = None
    ) -> CycleIndicators:
        """计算周期指标"""
        indicators = CycleIndicators()
        
        if klines is None or klines.empty:
            return indicators
            
        # SMA位置
        close = klines['close'].values
        sma_200 = self._sma(close, 200)
        sma_50 = self._sma(close, 50)
        
        if sma_200[-1] > 0:
            indicators.sma_200_position = 1.0 if close[-1] > sma_200[-1] else 0.0
            
        # 50/200均线交叉
        if len(sma_200) > 1 and sma_50[-2] < sma_200[-2] and sma_50[-1] > sma_200[-1]:
            indicators.sma_50_200_cross = 1.0  # 金叉
        elif len(sma_200) > 1 and sma_50[-2] > sma_200[-2] and sma_50[-1] < sma_200[-1]:
            indicators.sma_50_200_cross = -1.0  # 死叉
            
        # ATR百分位
        atr = self._atr(klines, 14)
        if len(atr) > 0:
            atr_percentile = (atr[-1] - np.min(atr[-100:])) / (np.max(atr[-100:]) - np.min(atr[-100:]) + 1e-8)
            indicators.atr_percentile = min(1.0, max(0.0, atr_percentile))
            
        # 成交量趋势
        if volume_data is not None and not volume_data.empty:
            vol = volume_data['volume'].values
            if len(vol) > 10:
                indicators.volume_trend = (np.mean(vol[-10:]) - np.mean(vol[-30:])) / (np.mean(vol[-30:]) + 1e-8)
                
        # OI趋势
        if oi_data is not None and not oi_data.empty:
            oi_col = "oi" if "oi" in oi_data.columns else "open_interest"
            if oi_col in oi_data.columns:
                oi = oi_data[oi_col].values
                if len(oi) > 10:
                    indicators.oi_trend = (np.mean(oi[-10:]) - np.mean(oi[-30:])) / (np.mean(oi[-30:]) + 1e-8)
                
        # 平均资金费率
        if funding_history:
            indicators.funding_rate_avg = np.mean(funding_history[-50:])
            
        return indicators
    
    def _calculate_cycle_score(self, indicators: CycleIndicators) -> float:
        """计算周期评分（增强版：加入 ADX、价格结构、动量斜率）"""
        score = 0.0

        # 均线位置权重 20%（原 30%，让出部分给新因子）
        score += indicators.sma_200_position * 0.20

        # 均线交叉权重 15%（原 20%）
        score += (indicators.sma_50_200_cross + 1) / 2 * 0.15

        # 资金费率权重 15%（原 20%，加密市场 funding 波动大，降低权重）
        if indicators.funding_rate_avg > 0.0003:
            score += 0.15
        elif indicators.funding_rate_avg < -0.0003:
            score -= 0.15
        elif indicators.funding_rate_avg > 0.0001:
            score += 0.08
        elif indicators.funding_rate_avg < -0.0001:
            score -= 0.08

        # OI趋势权重 10%（原 15%）
        score += np.tanh(indicators.oi_trend) * 0.10

        # 成交量趋势权重 10%（原 15%）
        score += np.tanh(indicators.volume_trend) * 0.10

        # [新] ADX 趋势强度 15%
        adx = getattr(indicators, 'adx', 0.0)
        if adx > 25:
            score += 0.15 * min(1.0, (adx - 15) / 30)
        elif adx < 15:
            score -= 0.05

        # [新] 价格结构 10%（Higher High / Higher Low）
        hh = getattr(indicators, 'higher_highs', False)
        hl = getattr(indicators, 'higher_lows', False)
        lh = getattr(indicators, 'lower_highs', False)
        ll = getattr(indicators, 'lower_lows', False)
        if hh and hl:
            score += 0.10
        elif lh and ll:
            score -= 0.10

        # [新] 动量斜率 5%（EMA50 近期变化率）
        slope = getattr(indicators, 'ema50_slope', 0.0)
        score += np.tanh(slope * 50) * 0.05

        return score
    
    def calculate_risk_budget(
        self,
        cycle: MarketCycle,
        account_balance: float,
        current_drawdown: float = 0.0
    ) -> RiskBudget:
        """
        根据市场周期计算风险预算
        
        Args:
            cycle: 市场周期
            account_balance: 账户余额
            current_drawdown: 当前回撤
            
        Returns:
            风险预算配置
        """
        budget = RiskBudget()
        
        # 根据周期调整风险参数
        if cycle == MarketCycle.BULL_TREND:
            budget.trend_direction = 1
            budget.max_daily_loss_pct = 0.05
            budget.max_position_size = 0.30
            
        elif cycle == MarketCycle.BEAR_TREND:
            budget.trend_direction = -1
            budget.max_daily_loss_pct = 0.03  # 更严格的止损
            budget.max_position_size = 0.20   # 更小的仓位
            
        elif cycle == MarketCycle.HIGH_VOLATILITY:
            budget.trend_direction = 0
            budget.max_daily_loss_pct = 0.03
            budget.max_position_size = 0.15
            budget.min_cash_reserve = 0.20  # 更高现金储备
            
        elif cycle == MarketCycle.LOW_VOLATILITY:
            budget.trend_direction = 0
            budget.max_daily_loss_pct = 0.05
            budget.max_position_size = 0.40  # 可以更大胆
            
        else:  # ACCUMULATION or UNKNOWN
            budget.trend_direction = 0
            budget.max_daily_loss_pct = 0.03
            budget.max_position_size = 0.25
            
        # 根据回撤调整
        if current_drawdown > 0.10:
            budget.max_daily_loss_pct *= 0.5
            budget.max_position_size *= 0.7
            budget.min_cash_reserve += 0.05
            
        return budget
    
    def plan(
        self,
        klines: pd.DataFrame,
        funding_history: List[float],
        volume_data: pd.DataFrame,
        oi_data: Optional[pd.DataFrame],
        account_balance: float,
        current_drawdown: float = 0.0,
        strategic_context: Optional[Dict] = None
    ) -> PlanningResult:
        """
        执行完整的中长期规划

        Args:
            klines: K线数据
            funding_history: 资金费率历史
            volume_data: 成交量数据
            oi_data: OI数据（可选）
            account_balance: 账户余额
            current_drawdown: 当前回撤
            strategic_context: 战略分析师提供的上下文（可选）

        Returns:
            规划结果
        """
        result = PlanningResult()

        # 1. 分析市场周期
        cycle, confidence, indicators = self.analyze_market_cycle(
            klines, funding_history, volume_data, oi_data
        )
        result.market_cycle = cycle
        result.cycle_confidence = confidence

        # 2. 计算风险预算
        result.risk_budget = self.calculate_risk_budget(
            cycle, account_balance, current_drawdown
        )

        # 3. 应用战略分析师增强（如果可用）
        if strategic_context and strategic_context.get("macro_confidence", 0) >= 0.6:
            result = self._apply_strategic_enhancement(result, strategic_context)

        # 4. 设置仓位偏向
        if result.risk_budget.trend_direction > 0:
            result.position_bias = "long"
        elif result.risk_budget.trend_direction < 0:
            result.position_bias = "short"
        else:
            result.position_bias = "neutral"

        # 5. 计算建议杠杆
        result.recommended_leverage = self._calculate_leverage(cycle, confidence)

        # 6. 识别关键价位
        result.key_levels = self._identify_key_levels(klines)

        # 7. 检测周期转换警告
        result.regime_transition_warning = self._check_transition_warning(
            indicators, cycle
        )
        # 战略分析师的体制转换信号也纳入
        if strategic_context and strategic_context.get("regime_transition_signal"):
            result.regime_transition_warning = True

        # 记录周期历史
        self.cycle_history.append({
            "timestamp": datetime.now(timezone.utc),
            "cycle": cycle.value,
            "confidence": confidence,
            "position_bias": result.position_bias
        })

        # 保持历史记录在合理范围
        if len(self.cycle_history) > 1000:
            self.cycle_history = self.cycle_history[-500:]

        logger.info(f"[LongTermPlanner] Cycle: {cycle.value}, "
                   f"Confidence: {confidence:.2%}, "
                   f"Bias: {result.position_bias}"
                   f"{f', MacroRegime: {result.macro_regime}' if result.macro_regime != 'unknown' else ''}")

        return result
    
    def _calculate_leverage(self, cycle: MarketCycle, confidence: float) -> float:
        """计算建议杠杆（范围 5x-20x，输出整数）"""
        base_leverage = 10.0

        if cycle == MarketCycle.BULL_TREND:
            base_leverage = 15.0
        elif cycle == MarketCycle.BEAR_TREND:
            base_leverage = 10.0
        elif cycle == MarketCycle.HIGH_VOLATILITY:
            base_leverage = 8.0
        elif cycle == MarketCycle.LOW_VOLATILITY:
            base_leverage = 15.0

        adjusted = base_leverage * (0.8 + confidence * 0.4)

        return float(min(20, max(5, round(adjusted))))

    def _apply_strategic_enhancement(
        self,
        result: PlanningResult,
        strategic_context: Dict
    ) -> PlanningResult:
        """
        应用战略分析师的增强信息到规划结果

        当战略分析师的置信度 >= 0.6 时，用宏观 risk_on_score
        调整风险预算和方向建议
        """
        result.macro_regime = strategic_context.get("macro_regime", "unknown")
        result.strategic_context = strategic_context

        # 应用风险预算调整系数
        adjustment = strategic_context.get("risk_budget_adjustment", 1.0)
        if adjustment != 1.0:
            result.risk_budget.max_position_size *= adjustment
            result.risk_budget.max_daily_loss_pct *= adjustment
            # 确保不超出安全范围
            result.risk_budget.max_position_size = min(0.40, max(0.10, result.risk_budget.max_position_size))
            result.risk_budget.max_daily_loss_pct = min(0.08, max(0.01, result.risk_budget.max_daily_loss_pct))

        return result

    def _identify_key_levels(self, klines: pd.DataFrame) -> Dict[str, float]:
        """识别关键支撑/阻力位"""
        if klines is None or klines.empty or len(klines) < 50:
            return {}
            
        high = klines['high'].values
        low = klines['low'].values
        close = klines['close'].values
        
        # 近期高低点
        recent_high = np.max(high[-50:])
        recent_low = np.min(low[-50:])
        current_price = close[-1]
        
        # 支撑位
        support_levels = [
            recent_low,
            np.percentile(low[-50:], 25),
            np.mean(low[-20:])
        ]
        
        # 阻力位
        resistance_levels = [
            recent_high,
            np.percentile(high[-50:], 75),
            np.mean(high[-20:])
        ]
        
        return {
            "current_price": current_price,
            "nearest_support": min(support_levels),
            "nearest_resistance": min(resistance_levels),
            "strong_support": max(support_levels),
            "strong_resistance": max(resistance_levels)
        }
    
    def _check_transition_warning(
        self, 
        indicators: CycleIndicators, 
        current_cycle: MarketCycle
    ) -> bool:
        """检查是否有周期转换警告"""
        # 均线即将交叉
        if abs(indicators.sma_50_200_cross) > 0.5:
            return True
            
        # 资金费率极端
        if abs(indicators.funding_rate_avg) > 0.0005:
            return True
            
        return False
    
    def _sma(self, data: np.ndarray, period: int) -> np.ndarray:
        """简单移动平均"""
        if len(data) < period:
            period = len(data)
        sma = np.convolve(data, np.ones(period), mode='valid') / period
        return np.concatenate([np.full(period - 1, np.nan), sma])
    
    def _atr(self, klines: pd.DataFrame, period: int) -> np.ndarray:
        """ATR指标"""
        high = klines['high'].values
        low = klines['low'].values
        close = klines['close'].values
        
        tr = np.maximum(
            high[1:] - low[1:],
            np.abs(high[1:] - close[:-1]),
            np.abs(low[1:] - close[:-1])
        )
        atr = np.zeros(len(close))
        atr[period] = np.mean(tr[:period])
        for i in range(period + 1, len(close)):
            atr[i] = (atr[i-1] * (period - 1) + tr[i-1]) / period
            
        return atr


# 全局实例
long_term_planner = LongTermPlanner()
