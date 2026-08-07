"""AI Factor: Inverse ADX Trend Strength | Confidence: 60%
Based on ADX indicator reverse signal. When ADX < 20, market is ranging,
trend unclear, shorting risk high; factor outputs positive.
When ADX > 50, trend strong, factor outputs negative.
Thus suggests avoiding shorts in weak trends.
"""
import pandas as pd
import numpy as np
from ..factor_base import BaseFactor, FactorMetadata


class InverseADXTrendStrength(BaseFactor):
    def get_metadata(self):
        return FactorMetadata(
            factor_id="ai_gen_adxinv",
            name="Inverse ADX (Trend Strength)",
            display_name="Inverse ADX Trend Strength",
            description=(
                "AI-generated factor: inverse ADX signal. "
                "ADX<20 market ranging -> positive factor (avoid short). "
                "ADX>50 strong trend -> negative factor. "
                "Confidence: 60%."
            ),
            category="technical",
            subcategory="trend",
            version="1.0.0-ai",
            author="AI Generated (D7)",
        )

    def calculate(self, data):
        high, low, close = data["high"], data["low"], data["close"]
        # Calculate +DI and -DI
        true_range = pd.concat(
            [high - low, abs(high - close.shift()), abs(low - close.shift())],
            axis=1,
        ).max(axis=1)
        atr = true_range.rolling(14).mean()
        up_move = high - high.shift()
        down_move = low.shift() - low
        pos = ((up_move > down_move) & (up_move > 0)) * up_move
        neg = ((down_move > up_move) & (down_move > 0)) * down_move
        pos_sum = pos.rolling(14).sum()
        neg_sum = neg.rolling(14).sum()
        pdi = 100 * pos_sum / (atr * 14 + 1e-10)
        ndi = 100 * neg_sum / (atr * 14 + 1e-10)
        dx = 100 * abs(pdi - ndi) / (pdi + ndi + 1e-10)
        adx = dx.rolling(14).mean()
        # Map: ADX=0 -> factor=1, ADX=50 -> factor=0, ADX=100 -> factor=-1
        factor = 1 - 2 * (adx / 50).clip(0, 1)
        return factor.fillna(0).clip(-1, 1)
