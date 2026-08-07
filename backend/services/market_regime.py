"""
MarketRegimeClassifier — 增强版市场状态分类器

6种市场状态识别 + 状态→策略参数映射。
纯 NumPy/Pandas 实现，不依赖ML库。

设计文档: SYSTEM_UPGRADE_DESIGN_V3.md 第4.4节
"""

from dataclasses import dataclass, field
from typing import Dict
from enum import Enum

import numpy as np
import pandas as pd


class MarketRegime(Enum):
    TRENDING_UP = "trending_up"
    TRENDING_DOWN = "trending_down"
    RANGING = "ranging"
    HIGH_VOLATILITY = "high_volatility"
    LOW_VOLATILITY = "low_volatility"
    CRASH = "crash"


@dataclass
class RegimeClassification:
    """市场状态分类结果"""
    regime: MarketRegime
    confidence: float               # 0~1
    features: Dict[str, float] = field(default_factory=dict)
    transition_prob: Dict[str, float] = field(default_factory=dict)


# 市场状态 → 策略参数映射表
REGIME_STRATEGY_MAP = {
    MarketRegime.TRENDING_UP: {
        'preferred_nature': 'trend_follow',
        'entry_factors': ['ema_trend', 'momentum', 'supertrend'],
        'param_overrides': {
            'stop_loss_pct': (0.03, 0.06),
            'take_profit_pct': (0.08, 0.20),
            'leverage': (2, 5),
            'trailing_stop': True,
        },
        'risk_multiplier': 1.2,
    },
    MarketRegime.TRENDING_DOWN: {
        'preferred_nature': 'trend_follow',
        'entry_factors': ['ema_trend', 'momentum', 'funding_rate'],
        'param_overrides': {
            'stop_loss_pct': (0.02, 0.05),
            'take_profit_pct': (0.06, 0.15),
            'leverage': (1, 3),
            'prefer_short': True,
        },
        'risk_multiplier': 0.8,
    },
    MarketRegime.RANGING: {
        'preferred_nature': 'swing',
        'entry_factors': ['rsi', 'bb_width', 'zscore'],
        'param_overrides': {
            'stop_loss_pct': (0.02, 0.04),
            'take_profit_pct': (0.03, 0.08),
            'leverage': (1, 3),
        },
        'risk_multiplier': 1.0,
    },
    MarketRegime.HIGH_VOLATILITY: {
        'preferred_nature': 'scalp',
        'entry_factors': ['atr', 'volume_zscore', 'parkinson_vol'],
        'param_overrides': {
            'stop_loss_pct': (0.01, 0.03),
            'take_profit_pct': (0.02, 0.06),
            'leverage': (1, 2),
        },
        'risk_multiplier': 0.5,
    },
    MarketRegime.LOW_VOLATILITY: {
        'preferred_nature': 'position',
        'entry_factors': ['ema_trend', 'sma_cross', 'funding_rate'],
        'param_overrides': {
            'stop_loss_pct': (0.03, 0.08),
            'take_profit_pct': (0.10, 0.25),
            'leverage': (2, 5),
        },
        'risk_multiplier': 1.5,
    },
    MarketRegime.CRASH: {
        'preferred_nature': 'scalp',
        'entry_factors': ['zscore', 'volume_zscore', 'funding_rate_extreme'],
        'param_overrides': {
            'stop_loss_pct': (0.01, 0.02),
            'take_profit_pct': (0.02, 0.05),
            'leverage': (1, 1),
        },
        'risk_multiplier': 0.3,
    },
}


class MarketRegimeClassifier:
    """
    基于规则的市场状态分类器

    特征：波动率、趋势强度、SMA交叉、波动率分位数
    分类规则优先级：崩盘 > 趋势 > 高/低波动 > 震荡
    """

    def classify(self, klines: pd.DataFrame, lookback: int = 100) -> RegimeClassification:
        """分类市场状态"""
        if klines is None or len(klines) < lookback:
            return RegimeClassification(
                regime=MarketRegime.RANGING,
                confidence=0.3,
                features={},
            )

        close = klines['close'].values[-lookback:]
        high = klines['high'].values[-lookback:]
        low = klines['low'].values[-lookback:]

        # 特征计算
        returns = np.diff(np.log(close))
        raw_vol = float(np.std(returns))
        volatility = raw_vol * np.sqrt(365 * 24)  # 年化波动率
        trend = float((close[-1] - close[0]) / close[0])

        sma20 = float(np.mean(close[-20:]))
        sma50 = float(np.mean(close[-min(50, len(close)):])) if len(close) >= 10 else sma20
        trend_strength = abs(sma20 - sma50) / (sma50 + 1e-10)

        # 波动率分位数（使用原始波动率，与分段波动率量纲一致）
        hist_vol = []
        step = max(5, len(returns) // 20)
        for i in range(0, len(returns) - 20, step):
            seg_vol = float(np.std(returns[i:i + 20]))
            hist_vol.append(seg_vol)
        vol_percentile = (
            sum(1 for v in hist_vol if v < raw_vol) / (len(hist_vol) + 1)
            if hist_vol else 0.5
        )

        features = {
            'volatility': volatility,
            'trend': trend,
            'trend_strength': trend_strength,
            'vol_percentile': vol_percentile,
        }

        # 规则分类
        if trend < -0.15 and volatility > 1.0:
            regime = MarketRegime.CRASH
            conf = 0.9
        elif trend_strength > 0.03 and trend > 0.05:
            regime = MarketRegime.TRENDING_UP
            conf = min(trend_strength * 10, 0.95)
        elif trend_strength > 0.03 and trend < -0.05:
            regime = MarketRegime.TRENDING_DOWN
            conf = min(trend_strength * 10, 0.95)
        elif vol_percentile > 0.8:
            regime = MarketRegime.HIGH_VOLATILITY
            conf = vol_percentile
        elif vol_percentile < 0.2:
            regime = MarketRegime.LOW_VOLATILITY
            conf = 1 - vol_percentile
        else:
            regime = MarketRegime.RANGING
            conf = 0.6

        return RegimeClassification(
            regime=regime,
            confidence=conf,
            features=features,
            transition_prob={},
        )

    def get_strategy_params(self, classification: RegimeClassification) -> Dict:
        """根据市场状态获取策略参数建议"""
        return REGIME_STRATEGY_MAP.get(classification.regime, REGIME_STRATEGY_MAP[MarketRegime.RANGING])
