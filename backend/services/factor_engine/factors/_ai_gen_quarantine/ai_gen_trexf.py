"""AI因子: 趋势衰竭因子 | 置信:60% | 衡量价格偏离中期均线的极端程度，偏离过大预示反转。计算(收盘价-EMA20)/ATR(14)，标准化至[-1,1]。正值超买，负值超卖。"""
import pandas as pd
import numpy as np
from backend.services.factor_engine.factor_base import BaseFactor, FactorMetadata
from backend.services.factor_engine.factor_registry import register_factor


@register_factor()
class TrendExhaustionFactor(BaseFactor):
    """衡量价格偏离中期均线的极端程度，偏离过大预示反转。计算(收盘价-EMA20)/ATR(14)，标准化至[-1,1]。正值超买，负值超卖。"""

    def get_metadata(self) -> FactorMetadata:
        return FactorMetadata(
            factor_id="ai_gen_trexf",
            name="Trend Exhaustion Factor",
            display_name="趋势衰竭因子",
            description="衡量价格偏离中期均线的极端程度，偏离过大预示反转。计算(收盘价-EMA20)/ATR(14)，标准化至[-1,1]。正值超买，负值超卖。",
            category="technical",
            subcategory="mean_reversion",
            version="1.0.0-ai",
            author="AI Generated (D7)",
        )

    def calculate(self, data):
        import pandas as pd
        import numpy as np
        close = data['close']
        high = data['high']
        low = data['low']
        ema20 = close.ewm(span=20, adjust=False).mean()
        tr = pd.concat([high - low, (high - close.shift()).abs(), (low - close.shift()).abs()], axis=1).max(axis=1)
        atr14 = tr.rolling(14).mean()
        raw = (close - ema20) / atr14.replace(0, np.nan)
        result = (raw.clip(-2, 2) / 2).fillna(0)
        return result
