"""AI因子: 止损波动过滤 | 置信:60% | 针对sl亏损模式：止损触发往往发生在高波动但方向不明的regime=unknown环境。该因子识别价格在窄幅区间内反复震荡且成交量萎缩的情况，此时趋势方向不确定，应反向操作。"""
import pandas as pd
import numpy as np
from backend.services.factor_engine.factor_base import BaseFactor, FactorMetadata
from backend.services.factor_engine.factor_registry import register_factor


@register_factor()
class Stoplossregimefilter(BaseFactor):
    """针对sl亏损模式：止损触发往往发生在高波动但方向不明的regime=unknown环境。该因子识别价格在窄幅区间内反复震荡且成交量萎缩的情况，此时趋势方向不确定，应反向操作。"""

    def get_metadata(self) -> FactorMetadata:
        return FactorMetadata(
            factor_id="ai_gen_slr",
            name="StopLossRegimeFilter",
            display_name="止损波动过滤",
            description="针对sl亏损模式：止损触发往往发生在高波动但方向不明的regime=unknown环境。该因子识别价格在窄幅区间内反复震荡且成交量萎缩的情况，此时趋势方向不确定，应反向操作。",
            category="technical",
            subcategory="volatility",
            version="1.0.0-ai",
            author="AI Generated (D7)",
        )

    def calculate(self, data):
        tr = (data['high'] - data['low']).rolling(10).mean()
        atr = data['close'].pct_change().rolling(10).std() * data['close']
        range_ratio = tr / (atr + 1e-9)
        vol_ratio = data['volume'] / data['volume'].rolling(20).mean()
        result = (range_ratio - 1) * (1 - vol_ratio).clip(0, 1)
        result = result.clip(-1, 1)
        return result
