"""AI因子: 趋势强度亏损比 | 置信:55% | 空头亏损多于多头，且多为超时或止损，说明逆势交易在unknown regime中代价高。该因子用过去N日趋势强度（价格变化/波动）的符号与当前收益方向对比，当趋势强时反向交易应被抑制。"""
import pandas as pd
import numpy as np
from backend.services.factor_engine.factor_base import BaseFactor, FactorMetadata
from backend.services.factor_engine.factor_registry import register_factor


@register_factor()
class Trendstrengthlossratio(BaseFactor):
    """空头亏损多于多头，且多为超时或止损，说明逆势交易在unknown regime中代价高。该因子用过去N日趋势强度（价格变化/波动）的符号与当前收益方向对比，当趋势强时反向交易应被抑制。"""

    def get_metadata(self) -> FactorMetadata:
        return FactorMetadata(
            factor_id="ai_gen_tslr",
            name="TrendStrengthLossRatio",
            display_name="趋势强度亏损比",
            description="空头亏损多于多头，且多为超时或止损，说明逆势交易在unknown regime中代价高。该因子用过去N日趋势强度（价格变化/波动）的符号与当前收益方向对比，当趋势强时反向交易应被抑制。",
            category="composite",
            subcategory="trend",
            version="1.0.0-ai",
            author="AI Generated (D7)",
        )

    def calculate(self, data):
        ret = data['close'].pct_change(10)
        vol = data['close'].pct_change().rolling(10).std()
        trend = ret / (vol + 1e-9)
        result = (-trend).clip(-1, 1)
        return result
