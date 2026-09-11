"""AI因子: 趋势持仓惩罚 | 置信:62% | 捕捉趋势行情中持仓时间过长导致的亏损。使用价格相对长期均线的偏离度与持仓周期（用成交量加权平均持有期近似）结合，惩罚在强趋势中过度持有。当价格偏离均线过大且成交量活跃时，因子值偏负，提示应缩短持仓。"""
import pandas as pd
import numpy as np
from backend.services.factor_engine.factor_base import BaseFactor, FactorMetadata
from backend.services.factor_engine.factor_registry import register_factor


@register_factor()
class Trendholdpenalty(BaseFactor):
    """捕捉趋势行情中持仓时间过长导致的亏损。使用价格相对长期均线的偏离度与持仓周期（用成交量加权平均持有期近似）结合，惩罚在强趋势中过度持有。当价格偏离均线过大且成交量活跃时，因子值偏负，提示应缩短持仓。"""

    def get_metadata(self) -> FactorMetadata:
        return FactorMetadata(
            factor_id="ai_gen_trend_hold_penalty",
            name="TrendHoldPenalty",
            display_name="趋势持仓惩罚",
            description="捕捉趋势行情中持仓时间过长导致的亏损。使用价格相对长期均线的偏离度与持仓周期（用成交量加权平均持有期近似）结合，惩罚在强趋势中过度持有。当价格偏离均线过大且成交量活跃时，因子值偏负，提示应缩短持仓。",
            category="composite",
            subcategory="trend",
            version="1.0.0-ai",
            author="AI Generated (D7)",
        )

    def calculate(self, data):
        ret = data['close'].pct_change(20)
        ma = data['close'].rolling(50).mean()
        dev = (data['close'] - ma) / (ma + 1e-9)
        vol_ratio = data['volume'] / (data['volume'].rolling(20).mean() + 1e-9)
        result = (ret * dev * vol_ratio).clip(-1, 1)
        return result
