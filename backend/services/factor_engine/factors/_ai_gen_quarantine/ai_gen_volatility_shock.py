"""AI因子: 波动率突变 | 置信:65% | 检测短期波动率相对于长期波动率的突变，当波动率突然放大或缩小且无明确趋势时，市场状态未知风险高。使用当前ATR与过去N日ATR均值的比值，并减去1，再clip到[-1,1]。"""
import pandas as pd
import numpy as np
from backend.services.factor_engine.factor_base import BaseFactor, FactorMetadata
from backend.services.factor_engine.factor_registry import register_factor


@register_factor()
class VolatilityShock(BaseFactor):
    """检测短期波动率相对于长期波动率的突变，当波动率突然放大或缩小且无明确趋势时，市场状态未知风险高。使用当前ATR与过去N日ATR均值的比值，并减去1，再clip到[-1,1]。"""

    def get_metadata(self) -> FactorMetadata:
        return FactorMetadata(
            factor_id="ai_gen_volatility_shock",
            name="VolatilityShock",
            display_name="波动率突变",
            description="检测短期波动率相对于长期波动率的突变，当波动率突然放大或缩小且无明确趋势时，市场状态未知风险高。使用当前ATR与过去N日ATR均值的比值，并减去1，再clip到[-1,1]。",
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
        short_atr = tr.rolling(5).mean()
        long_atr = tr.rolling(20).mean()
        ratio = short_atr / (long_atr + 1e-10)
        # 归一化：比例偏离1的程度，clip到[-1,1]
        result = (ratio - 1).clip(-1, 1)
        return result
