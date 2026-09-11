"""AI因子: 波动突破衰减 | 置信:58% | 针对止损亏损模式，识别价格突破后波动率急剧放大但缺乏持续性的情况。使用短期波动率与长期波动率的比值，结合价格在突破区间的相对位置，当波动率激增但价格未能有效突破时，因子值偏负，提示追突破风险高。"""
import pandas as pd
import numpy as np
from backend.services.factor_engine.factor_base import BaseFactor, FactorMetadata
from backend.services.factor_engine.factor_registry import register_factor


@register_factor()
class Volbreakoutfade(BaseFactor):
    """针对止损亏损模式，识别价格突破后波动率急剧放大但缺乏持续性的情况。使用短期波动率与长期波动率的比值，结合价格在突破区间的相对位置，当波动率激增但价格未能有效突破时，因子值偏负，提示追突破风险高。"""

    def get_metadata(self) -> FactorMetadata:
        return FactorMetadata(
            factor_id="ai_gen_vol_breakout_fade",
            name="VolBreakoutFade",
            display_name="波动突破衰减",
            description="针对止损亏损模式，识别价格突破后波动率急剧放大但缺乏持续性的情况。使用短期波动率与长期波动率的比值，结合价格在突破区间的相对位置，当波动率激增但价格未能有效突破时，因子值偏负，提示追突破风险高。",
            category="technical",
            subcategory="volatility",
            version="1.0.0-ai",
            author="AI Generated (D7)",
        )

    def calculate(self, data):
        short_vol = data['close'].pct_change().rolling(5).std()
        long_vol = data['close'].pct_change().rolling(30).std()
        vol_ratio = short_vol / (long_vol + 1e-9)
        high_range = data['high'].rolling(20).max()
        low_range = data['low'].rolling(20).min()
        pos = (data['close'] - low_range) / (high_range - low_range + 1e-9)
        result = (vol_ratio * (pos - 0.5) * 2).clip(-1, 1)
        return result
