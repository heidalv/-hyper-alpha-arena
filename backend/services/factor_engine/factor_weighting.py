"""
Factor Weighting - 动态因子权重

提供基于市场状态的因子权重自适应调整：
1. 市场状态检测
2. 状态-因子权重映射
3. 动态权重计算
4. 权重平滑过渡

Author: Hyper-Alpha-Arena
"""

import logging
from typing import Dict, List, Optional, Tuple
from dataclasses import dataclass, field
from enum import Enum
import numpy as np

from .base_factors import FactorEngine, FactorCategory, FactorValue

logger = logging.getLogger(__name__)


class MarketRegime(Enum):
    """市场状态"""
    BREAKOUT = "breakout"  # 突破
    CONTINUATION = "continuation"  # 延续
    REVERSAL = "reversal"  # 反转
    ABSORPTION = "absorption"  # 吸筹
    EXHAUSTION = "exhaustion"  # 衰竭
    NOISE = "noise"  # 震荡


@dataclass
class RegimeWeights:
    """市场状态下的因子权重"""
    regime: MarketRegime
    weights: Dict[str, float]
    position_size_modifier: float
    stop_loss_atr_multiple: float
    take_profit_atr_multiple: float
    description: str


@dataclass
class AdaptiveWeights:
    """自适应权重结果"""
    weights: Dict[str, float]
    regime: MarketRegime
    confidence: float
    regime_indicators: Dict[str, float]
    transition_smoothed: bool = False


class DynamicFactorWeighting:
    """
    动态因子权重调整器
    
    根据市场状态自动调整因子权重，
    优化AI决策的因子输入
    """
    
    def __init__(self, factor_engine: FactorEngine):
        self.engine = factor_engine
        self.current_regime: Optional[MarketRegime] = None
        self.previous_weights: Dict[str, float] = {}
        self.transition_alpha = 0.3  # 权重平滑系数
        
        self._init_regime_weights()
    
    def _init_regime_weights(self):
        """初始化各市场状态的因子权重"""
        self.REGIME_WEIGHTS = {
            MarketRegime.BREAKOUT: RegimeWeights(
                regime=MarketRegime.BREAKOUT,
                weights={
                    'supertrend': 0.15,
                    'sma_cross': 0.12,
                    'momentum': 0.12,
                    'volume_zscore': 0.10,
                    'atr': 0.08,
                    'taker_ratio': 0.10,
                    'oi_delta': 0.08,
                    'macd': 0.08,
                    'rsi': 0.05,
                    'bb_width': 0.05,
                    'funding_rate': 0.05,
                    'obv': 0.02
                },
                position_size_modifier=1.0,
                stop_loss_atr_multiple=1.5,
                take_profit_atr_multiple=2.5,
                description="突破行情：趋势因子权重最高，动量因子辅助"
            ),
            MarketRegime.CONTINUATION: RegimeWeights(
                regime=MarketRegime.CONTINUATION,
                weights={
                    'trend': 0.15,
                    'momentum': 0.12,
                    'sma_cross': 0.10,
                    'ema_trend': 0.10,
                    'atr': 0.10,
                    'volume_zscore': 0.08,
                    'macd': 0.08,
                    'rsi': 0.06,
                    'taker_ratio': 0.06,
                    'adx': 0.05,
                    'hv': 0.05,
                    'cvd_ratio': 0.05
                },
                position_size_modifier=0.9,
                stop_loss_atr_multiple=1.5,
                take_profit_atr_multiple=2.0,
                description="延续行情：趋势和动量因子并重"
            ),
            MarketRegime.REVERSAL: RegimeWeights(
                regime=MarketRegime.REVERSAL,
                weights={
                    'rsi': 0.15,
                    'bb_width': 0.12,
                    'zscore': 0.12,
                    'momentum': 0.10,
                    'atr_ratio': 0.08,
                    'funding_rate': 0.08,
                    'volume_zscore': 0.08,
                    'macd': 0.06,
                    'adx': 0.05,
                    'supertrend': 0.04,
                    'taker_ratio': 0.04,
                    'obv': 0.03
                },
                position_size_modifier=0.6,
                stop_loss_atr_multiple=2.0,
                take_profit_atr_multiple=1.5,
                description="反转行情：均值回归和超卖超买因子为主"
            ),
            MarketRegime.ABSORPTION: RegimeWeights(
                regime=MarketRegime.ABSORPTION,
                weights={
                    'volume_zscore': 0.15,
                    'taker_ratio': 0.12,
                    'cvd_ratio': 0.12,
                    'oi_delta': 0.10,
                    'funding_rate': 0.10,
                    'bb_width': 0.08,
                    'atr': 0.08,
                    'rsi': 0.06,
                    'momentum': 0.05,
                    'zscore': 0.05,
                    'parkinson_vol': 0.05,
                    'obv': 0.04
                },
                position_size_modifier=0.5,
                stop_loss_atr_multiple=2.5,
                take_profit_atr_multiple=2.0,
                description="吸筹/派发：成交量和资金流向因子最重要"
            ),
            MarketRegime.EXHAUSTION: RegimeWeights(
                regime=MarketRegime.EXHAUSTION,
                weights={
                    'rsi': 0.12,
                    'funding_rate': 0.12,
                    'momentum': 0.10,
                    'macd': 0.10,
                    'volume_zscore': 0.10,
                    'adx': 0.08,
                    'atr': 0.08,
                    'taker_ratio': 0.06,
                    'oi_delta': 0.06,
                    'bb_width': 0.05,
                    'zscore': 0.05,
                    'cvd_ratio': 0.03
                },
                position_size_modifier=0.5,
                stop_loss_atr_multiple=2.0,
                take_profit_atr_multiple=2.5,
                description="衰竭行情：风险控制因子权重提高"
            ),
            MarketRegime.NOISE: RegimeWeights(
                regime=MarketRegime.NOISE,
                weights={
                    'atr': 0.15,
                    'hv': 0.12,
                    'parkinson_vol': 0.12,
                    'bb_width': 0.10,
                    'volume_zscore': 0.10,
                    'rsi': 0.08,
                    'zscore': 0.08,
                    'momentum': 0.06,
                    'macd': 0.05,
                    'taker_ratio': 0.05,
                    'oi_delta': 0.05,
                    'cvd_ratio': 0.04
                },
                position_size_modifier=0.5,
                stop_loss_atr_multiple=2.0,
                take_profit_atr_multiple=1.2,
                description="震荡行情：波动率因子最重要，减少交易频率"
            )
        }
    
    def detect_regime(
        self, 
        factor_values: Dict[str, FactorValue],
        market_data: Optional[Dict] = None
    ) -> Tuple[MarketRegime, float, Dict[str, float]]:
        """
        检测当前市场状态
        
        Args:
            factor_values: 当前因子值
            market_data: 额外市场数据
            
        Returns:
            (市场状态, 置信度, 状态指标)
        """
        indicators = self._calculate_regime_indicators(factor_values, market_data)
        
        regime_scores = {
            MarketRegime.BREAKOUT: self._score_breakout(indicators),
            MarketRegime.CONTINUATION: self._score_continuation(indicators),
            MarketRegime.REVERSAL: self._score_reversal(indicators),
            MarketRegime.ABSORPTION: self._score_absorption(indicators),
            MarketRegime.EXHAUSTION: self._score_exhaustion(indicators),
            MarketRegime.NOISE: self._score_noise(indicators)
        }
        
        best_regime = max(regime_scores, key=regime_scores.get)
        confidence = regime_scores[best_regime]
        
        self.current_regime = best_regime
        
        return best_regime, confidence, indicators
    
    def _calculate_regime_indicators(
        self, 
        factor_values: Dict[str, FactorValue],
        market_data: Optional[Dict] = None
    ) -> Dict[str, float]:
        """计算状态检测指标"""
        indicators = {}
        
        for name, factor in factor_values.items():
            indicators[f"{name}_value"] = factor.value
            indicators[f"{name}_normalized"] = factor.normalized
        
        if market_data:
            indicators.update({
                'volatility_level': market_data.get('volatility', 0.5),
                'volume_level': market_data.get('volume', 0.5),
                'trend_strength': market_data.get('trend_strength', 0.5),
                'funding_deviation': market_data.get('funding_deviation', 0.0)
            })
        
        if 'supertrend' in factor_values:
            indicators['supertrend'] = factor_values['supertrend'].value
        if 'sma_cross' in factor_values:
            indicators['sma_cross'] = factor_values['sma_cross'].value
        if 'momentum' in factor_values:
            indicators['momentum'] = factor_values['momentum'].value
        if 'rsi' in factor_values:
            indicators['rsi'] = factor_values['rsi'].value
        if 'bb_width' in factor_values:
            indicators['bb_width'] = factor_values['bb_width'].value
        if 'volume_zscore' in factor_values:
            indicators['volume_zscore'] = factor_values['volume_zscore'].value
        if 'atr' in factor_values:
            indicators['atr'] = factor_values['atr'].value
        if 'hv' in factor_values:
            indicators['hv'] = factor_values['hv'].value
        if 'adx' in factor_values:
            indicators['adx'] = factor_values['adx'].value
        if 'funding_rate' in factor_values:
            indicators['funding_rate'] = factor_values['funding_rate'].value
        
        return indicators
    
    def _score_breakout(self, ind: Dict[str, float]) -> float:
        """突破状态评分"""
        score = 0.0
        
        if ind.get('supertrend', 0) > 0.5:
            score += 0.25
        if abs(ind.get('sma_cross', 0)) > 0.02:
            score += 0.2
        if ind.get('adx', 20) > 25:
            score += 0.2
        if abs(ind.get('momentum', 0)) > 2:
            score += 0.15
        if ind.get('volume_zscore', 0) > 1:
            score += 0.2
        
        return min(1.0, score)
    
    def _score_continuation(self, ind: Dict[str, float]) -> float:
        """延续状态评分"""
        score = 0.0
        
        if 0.3 < ind.get('rsi', 50) < 70:
            score += 0.2
        if abs(ind.get('momentum', 0)) > 1:
            score += 0.2
        if ind.get('adx', 20) > 20:
            score += 0.2
        if abs(ind.get('sma_cross', 0)) > 0.01:
            score += 0.15
        if abs(ind.get('macd', 0)) > 0:
            score += 0.15
        
        return min(1.0, score)
    
    def _score_reversal(self, ind: Dict[str, float]) -> float:
        """反转状态评分"""
        score = 0.0
        
        rsi = ind.get('rsi', 50)
        if rsi < 30 or rsi > 70:
            score += 0.3
        if abs(ind.get('bb_width', 0)) > 0.05:
            score += 0.2
        if abs(ind.get('zscore', 0)) > 1.5:
            score += 0.2
        if ind.get('funding_rate', 0) > 0.1:
            score += 0.15
        if abs(ind.get('momentum', 0)) > 3:
            score += 0.15
        
        return min(1.0, score)
    
    def _score_absorption(self, ind: Dict[str, float]) -> float:
        """吸筹状态评分"""
        score = 0.0
        
        if abs(ind.get('volume_zscore', 0)) > 1.5:
            score += 0.3
        if abs(ind.get('taker_ratio', 0)) > 0.2:
            score += 0.2
        if abs(ind.get('cvd_ratio', 0)) > 0.1:
            score += 0.2
        if ind.get('oi_delta', 0) > 5:
            score += 0.15
        if ind.get('funding_rate', 0) > 0.05:
            score += 0.15
        
        return min(1.0, score)
    
    def _score_exhaustion(self, ind: Dict[str, float]) -> float:
        """衰竭状态评分"""
        score = 0.0
        
        if ind.get('rsi', 50) > 80 or ind.get('rsi', 50) < 20:
            score += 0.3
        if ind.get('funding_rate', 0) > 0.2:
            score += 0.25
        if abs(ind.get('momentum', 0)) > 5:
            score += 0.2
        if ind.get('hv', 0) > 50:
            score += 0.15
        if abs(ind.get('macd', 0)) > 0.1:
            score += 0.1
        
        return min(1.0, score)
    
    def _score_noise(self, ind: Dict[str, float]) -> float:
        """震荡状态评分"""
        score = 0.0
        
        if ind.get('bb_width', 0) < 0.03:
            score += 0.25
        if ind.get('hv', 0) < 30:
            score += 0.2
        if ind.get('atr', 0) < 0.02:
            score += 0.15
        if abs(ind.get('momentum', 0)) < 1:
            score += 0.2
        if abs(ind.get('sma_cross', 0)) < 0.005:
            score += 0.2
        
        return min(1.0, score)
    
    def get_regime_weights(
        self, 
        regime: MarketRegime, 
        factor_values: Optional[Dict[str, FactorValue]] = None
    ) -> Dict[str, float]:
        """
        获取指定市场状态的基础权重
        
        Args:
            regime: 市场状态
            factor_values: 当前因子值（用于可选的微调）
            
        Returns:
            因子名称 -> 权重
        """
        regime_config = self.REGIME_WEIGHTS.get(regime)
        if not regime_config:
            return {}
        
        weights = regime_config.weights.copy()
        
        if factor_values:
            weights = self._fine_tune_weights(weights, factor_values)
        
        return weights
    
    def _fine_tune_weights(
        self, 
        base_weights: Dict[str, float], 
        factor_values: Dict[str, FactorValue]
    ) -> Dict[str, float]:
        """微调权重"""
        weights = base_weights.copy()
        
        for factor_name in list(weights.keys()):
            if factor_name in factor_values:
                factor = factor_values[factor_name]
                adjustment = min(0.1, abs(factor.normalized) * 0.05)
                
                if factor.normalized > 0:
                    weights[factor_name] = min(0.2, weights[factor_name] + adjustment)
                else:
                    weights[factor_name] = max(0.01, weights[factor_name] - adjustment * 0.5)
        
        total = sum(weights.values())
        if total > 0:
            weights = {k: v / total for k, v in weights.items()}
        
        return weights
    
    def calculate_adaptive_weights(
        self, 
        factor_values: Dict[str, FactorValue],
        market_data: Optional[Dict] = None,
        smooth_transition: bool = True
    ) -> AdaptiveWeights:
        """
        计算自适应权重
        
        Args:
            factor_values: 当前因子值
            market_data: 额外市场数据
            smooth_transition: 是否平滑过渡
            
        Returns:
            AdaptiveWeights对象
        """
        regime, confidence, indicators = self.detect_regime(factor_values, market_data)
        
        weights = self.get_regime_weights(regime, factor_values)
        
        if smooth_transition and self.previous_weights:
            weights = self._smooth_transition(weights, self.previous_weights)
        
        self.previous_weights = weights.copy()
        
        return AdaptiveWeights(
            weights=weights,
            regime=regime,
            confidence=confidence,
            regime_indicators=indicators,
            transition_smoothed=smooth_transition and bool(self.previous_weights)
        )
    
    def _smooth_transition(
        self, 
        new_weights: Dict[str, float], 
        old_weights: Dict[str, float]
    ) -> Dict[str, float]:
        """平滑过渡权重"""
        smoothed = {}
        
        all_factors = set(new_weights.keys()) | set(old_weights.keys())
        
        for factor in all_factors:
            new_w = new_weights.get(factor, 0.0)
            old_w = old_weights.get(factor, 0.0)
            smoothed[factor] = self.transition_alpha * new_w + (1 - self.transition_alpha) * old_w
        
        total = sum(smoothed.values())
        if total > 0:
            smoothed = {k: v / total for k, v in smoothed.items()}
        
        return smoothed
    
    def get_trading_parameters(self, regime: MarketRegime) -> Dict:
        """
        获取交易参数
        
        Returns:
            仓位大小、止损、止盈等参数
        """
        regime_config = self.REGIME_WEIGHTS.get(regime)
        if not regime_config:
            return {
                'position_size_modifier': 0.5,
                'stop_loss_atr_multiple': 2.0,
                'take_profit_atr_multiple': 2.0
            }
        
        return {
            'position_size_modifier': regime_config.position_size_modifier,
            'stop_loss_atr_multiple': regime_config.stop_loss_atr_multiple,
            'take_profit_atr_multiple': regime_config.take_profit_atr_multiple,
            'description': regime_config.description
        }
    
    def explain_weights(self, weights: Dict[str, float]) -> str:
        """解释权重分配"""
        if not self.current_regime:
            return "市场状态未知，使用默认权重"
        
        regime_config = self.REGIME_WEIGHTS[self.current_regime]
        
        explanation = [f"当前市场状态: {self.current_regime.value}"]
        explanation.append(f"置信度: {regime_config.description}")
        explanation.append("")
        explanation.append("权重分布:")
        
        sorted_weights = sorted(weights.items(), key=lambda x: x[1], reverse=True)
        for factor, weight in sorted_weights[:5]:
            explanation.append(f"  - {factor}: {weight:.2%}")
        
        params = self.get_trading_parameters(self.current_regime)
        explanation.append("")
        explanation.append(f"建议仓位: {params['position_size_modifier']:.0%}")
        explanation.append(f"建议止损: {params['stop_loss_atr_multiple']}x ATR")
        explanation.append(f"建议止盈: {params['take_profit_atr_multiple']}x ATR")
        
        return "\n".join(explanation)
    
    def reset_state(self):
        """重置状态"""
        self.current_regime = None
        self.previous_weights.clear()
        logger.info("[DynamicFactorWeighting] State reset")
    
    # ════════════════════════════════════════════════════════
    # V3 整合：因子权重自适应反馈
    # ════════════════════════════════════════════════════════
    
    def apply_feedback_adjustments(
        self,
        contributions: Dict[str, float],
        max_adjustment: float = 0.20,
    ) -> Dict[str, float]:
        """
        V3 整合：基于因子贡献度微调当前 regime 的权重。
        
        Args:
            contributions: 因子名 -> 贡献度分数（正值=正向贡献，负值=负向贡献）
            max_adjustment: 单次最大调整幅度（默认 20%）
            
        Returns:
            调整后的因子权重
        """
        if not contributions or not self.current_regime:
            return {}
        
        regime_config = self.REGIME_WEIGHTS.get(self.current_regime)
        if not regime_config:
            return {}
        
        adjusted = {}
        for factor_name, base_weight in regime_config.weights.items():
            contribution = contributions.get(factor_name, 0.0)
            
            # 计算调整量：贡献度 * 最大调整幅度
            adjustment = contribution * max_adjustment
            
            # 限制单次调整幅度
            adjustment = max(-max_adjustment, min(max_adjustment, adjustment))
            
            # 应用调整
            new_weight = base_weight + adjustment * base_weight
            new_weight = max(0.01, min(0.30, new_weight))  # 限制权重范围
            adjusted[factor_name] = round(new_weight, 4)
        
        # 归一化
        total = sum(adjusted.values())
        if total > 0:
            adjusted = {k: round(v / total, 4) for k, v in adjusted.items()}
        
        logger.info(
            f"[DynamicFactorWeighting] 权重反馈调整完成: "
            f"regime={self.current_regime.value}, "
            f"调整因子数={len([c for c in contributions.values() if abs(c) > 0.05])}"
        )
        return adjusted


# 全局实例
_factor_weighting: Optional[DynamicFactorWeighting] = None


def get_factor_weighting() -> DynamicFactorWeighting:
    """获取全局动态权重实例"""
    global _factor_weighting
    if _factor_weighting is None:
        from .base_factors import factor_engine
        _factor_weighting = DynamicFactorWeighting(factor_engine)
    return _factor_weighting


def get_adaptive_weights(
    factor_values: Dict[str, FactorValue],
    market_data: Optional[Dict] = None
) -> AdaptiveWeights:
    """便捷函数：获取自适应权重"""
    weighting = get_factor_weighting()
    return weighting.calculate_adaptive_weights(factor_values, market_data)
