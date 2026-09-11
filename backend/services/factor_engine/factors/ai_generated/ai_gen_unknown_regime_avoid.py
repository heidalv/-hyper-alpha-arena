"""AI因子: 未知状态回避 | 置信:50% | 所有亏损都发生在regime=unknown，说明当前因子在状态不明时容易失效。此因子通过检测价格与均线的偏离程度和成交量变化，识别市场是否处于明确趋势中。当价格在均线附近且成交量萎缩时，输出接近0（回避交易）；当价格明显偏离且成交量放大时，输出方向性信号。"""
import pandas as pd
import numpy as np
from backend.services.factor_engine.factor_base import BaseFactor, FactorMetadata
from backend.services.factor_engine.factor_registry import register_factor


@register_factor()
class Unknownregimeavoidance(BaseFactor):
    """所有亏损都发生在regime=unknown，说明当前因子在状态不明时容易失效。此因子通过检测价格与均线的偏离程度和成交量变化，识别市场是否处于明确趋势中。当价格在均线附近且成交量萎缩时，输出接近0（回避交易）；当价格明显偏离且成交量放大时，输出方向性信号。"""

    def get_metadata(self) -> FactorMetadata:
        return FactorMetadata(
            factor_id="ai_gen_unknown_regime_avoid",
            name="UnknownRegimeAvoidance",
            display_name="未知状态回避",
            description="所有亏损都发生在regime=unknown，说明当前因子在状态不明时容易失效。此因子通过检测价格与均线的偏离程度和成交量变化，识别市场是否处于明确趋势中。当价格在均线附近且成交量萎缩时，输出接近0（回避交易）；当价格明显偏离且成交量放大时，输出方向性信号。",
            category="behavioral",
            subcategory="trend",
            version="1.0.0-ai",
            author="AI Generated (D7)",
        )

    def calculate(self, data):
        ma = data['close'].rolling(20).mean()
        dev = (data['close'] - ma) / (ma + 1e-9)
        vol_ratio = data['volume'] / data['volume'].rolling(20).mean()
        result = (dev * 3 + (vol_ratio - 1) * 0.5).clip(-1, 1)
        return result
