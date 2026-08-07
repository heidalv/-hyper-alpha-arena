"""AI因子: 波动率突变反转因子 | 置信:60% | 检测短期波动率相对中期波动率的异常放大，放大后往往回归。计算(ATR5/ATR20 - 1)，取负值并标准化至[-1,1]。"""
import pandas as pd
import numpy as np
from backend.services.factor_engine.factor_base import BaseFactor, FactorMetadata
from backend.services.factor_engine.factor_registry import register_factor


@register_factor()
class VolatilitySpikeReversal(BaseFactor):
    """检测短期波动率相对中期波动率的异常放大，放大后往往回归。计算(ATR5/ATR20 - 1)，取负值并标准化至[-1,1]。"""

    def get_metadata(self) -> FactorMetadata:
        return FactorMetadata(
            factor_id="ai_gen_volrev",
            name="Volatility Spike Reversal",
            display_name="波动率突变反转因子",
            description="检测短期波动率相对中期波动率的异常放大，放大后往往回归。计算(ATR5/ATR20 - 1)，取负值并标准化至[-1,1]。",
            category="technical",
            subcategory="volatility",
            version="1.0.0-ai",
            author="AI Generated (D7)",
        )

    def calculate(self, data):
        import pandas as pd
        import numpy as np
        high = data['high']
        low = data['low']
        close = data['close']
        tr = pd.concat([high - low, (high - close.shift()).abs(), (low - close.shift()).abs()], axis=1).max(axis=1)
        atr5 = tr.rolling(5).mean()
        atr20 = tr.rolling(20).mean()
        ratio = atr5 / atr20.replace(0, np.nan) - 1.0
        result = (-ratio.clip(-1, 1)).fillna(0)
        return result
