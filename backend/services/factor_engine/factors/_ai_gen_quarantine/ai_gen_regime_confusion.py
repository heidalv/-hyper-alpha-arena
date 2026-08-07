"""AI因子: 市场状态混乱度指标 | 置信:65% | 结合ATR和ADX判断市场趋势强度与波动性。当ATR高而ADX低时，市场处于无序波动（regime=unknown），因子输出负值；反之，趋势明确时输出正值。"""
import pandas as pd
import numpy as np
from backend.services.factor_engine.factor_base import BaseFactor, FactorMetadata
from backend.services.factor_engine.factor_registry import register_factor


@register_factor()
class RegimeConfusionIndicator(BaseFactor):
    """结合ATR和ADX判断市场趋势强度与波动性。当ATR高而ADX低时，市场处于无序波动（regime=unknown），因子输出负值；反之，趋势明确时输出正值。"""

    def get_metadata(self) -> FactorMetadata:
        return FactorMetadata(
            factor_id="ai_gen_regime_confusion",
            name="Regime Confusion Indicator",
            display_name="市场状态混乱度指标",
            description="结合ATR和ADX判断市场趋势强度与波动性。当ATR高而ADX低时，市场处于无序波动（regime=unknown），因子输出负值；反之，趋势明确时输出正值。",
            category="technical",
            subcategory="volatility",
            version="1.0.0-ai",
            author="AI Generated (D7)",
        )

    def calculate(self, data):
        import numpy as np
        # ATR calculation
        high = data['high']
        low = data['low']
        close = data['close']
        tr = np.maximum(high - low, np.abs(high - close.shift(1)), np.abs(low - close.shift(1)))
        atr = tr.rolling(14).mean()
        # ADX calculation
        up = high.diff()
        down = -low.diff()
        dm_plus = np.where((up > down) & (up > 0), up, 0)
        dm_minus = np.where((down > up) & (down > 0), down, 0)
        tr_sum = tr.rolling(14).sum()
        di_plus = 100 * pd.Series(dm_plus).rolling(14).sum() / tr_sum
        di_minus = 100 * pd.Series(dm_minus).rolling(14).sum() / tr_sum
        dx = 100 * np.abs(di_plus - di_minus) / (di_plus + di_minus)
        adx = dx.rolling(14).mean()
        # Combine: normalize ATR and ADX to [0,1] using rolling z-score or rank
        atr_norm = (atr - atr.rolling(60).mean()) / atr.rolling(60).std()
        adx_norm = (adx - adx.rolling(60).mean()) / adx.rolling(60).std()
        # Factor: when adx low and atr high -> chaotic -> negative
        factor = -np.sign(atr_norm) * np.abs(adx_norm) * 0.5 + 0.5 * np.sign(adx_norm) * np.abs(atr_norm)
        # Clip to [-1,1]
        factor = factor.clip(-1, 1)
        return factor.fillna(0)
