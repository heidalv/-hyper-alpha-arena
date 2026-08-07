"""AI因子: 趋势强度衰减 | 置信:65% | 衡量趋势强度的变化速度。当趋势动能持续减弱时，市场容易进入震荡导致持仓超时亏损。因子值[-1,1]，负值表示趋势衰减风险高，正值表示趋势加速。"""
import pandas as pd
import numpy as np
from backend.services.factor_engine.factor_base import BaseFactor, FactorMetadata
from backend.services.factor_engine.factor_registry import register_factor


@register_factor()
class TrendStrengthDecay(BaseFactor):
    """衡量趋势强度的变化速度。当趋势动能持续减弱时，市场容易进入震荡导致持仓超时亏损。因子值[-1,1]，负值表示趋势衰减风险高，正值表示趋势加速。"""

    def get_metadata(self) -> FactorMetadata:
        return FactorMetadata(
            factor_id="ai_gen_tsd",
            name="Trend Strength Decay",
            display_name="趋势强度衰减",
            description="衡量趋势强度的变化速度。当趋势动能持续减弱时，市场容易进入震荡导致持仓超时亏损。因子值[-1,1]，负值表示趋势衰减风险高，正值表示趋势加速。",
            category="technical",
            subcategory="trend",
            version="1.0.0-ai",
            author="AI Generated (D7)",
        )

    def calculate(self, data):
        close = data['close']
        high = data['high']
        low = data['low']
        atr_period = 14
        ma_period = 20
        decay_lookback = 3
        # ATR
        tr = pd.DataFrame({
            'hl': high - low,
            'hc': (high - close.shift()).abs(),
            'lc': (low - close.shift()).abs()
        }).max(axis=1)
        atr = tr.rolling(atr_period).mean()
        # SMA
        sma = close.rolling(ma_period).mean()
        # trend strength
        trend_strength = (close - sma) / atr
        # decay: change over decay_lookback
        decay = trend_strength - trend_strength.shift(decay_lookback)
        # normalize to [-1, 1] via rolling percentile
        norm_period = 60
        rank = decay.rolling(norm_period).rank(pct=True)
        result = (rank - 0.5) * 2.0
        result = result.fillna(0).clip(-1, 1)
        return result
