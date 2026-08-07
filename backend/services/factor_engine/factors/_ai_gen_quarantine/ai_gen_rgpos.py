"""AI因子: 区间相对位置 | 置信:80% | 计算收盘价在近期最高最低区间内的相对位置。值接近0表示价格位于区间中部，方向不明确，容易发生持仓超时亏损；+1表示突破区间上沿，-1表示跌破区间下沿。"""
import pandas as pd
import numpy as np
from backend.services.factor_engine.factor_base import BaseFactor, FactorMetadata
from backend.services.factor_engine.factor_registry import register_factor


@register_factor()
class RangePosition(BaseFactor):
    """计算收盘价在近期最高最低区间内的相对位置。值接近0表示价格位于区间中部，方向不明确，容易发生持仓超时亏损；+1表示突破区间上沿，-1表示跌破区间下沿。"""

    def get_metadata(self) -> FactorMetadata:
        return FactorMetadata(
            factor_id="ai_gen_rgpos",
            name="Range Position",
            display_name="区间相对位置",
            description="计算收盘价在近期最高最低区间内的相对位置。值接近0表示价格位于区间中部，方向不明确，容易发生持仓超时亏损；+1表示突破区间上沿，-1表示跌破区间下沿。",
            category="technical",
            subcategory="mean_reversion",
            version="1.0.0-ai",
            author="AI Generated (D7)",
        )

    def calculate(self, data):
        high = data['high']
        low = data['low']
        close = data['close']
        period = 20
        highest = high.rolling(window=period).max()
        lowest = low.rolling(window=period).min()
        # 区间相对位置，原始范围[0,1]
        range_pos = (close - lowest) / (highest - lowest + 1e-9)
        # 映射到[-1,1]，中间为0，两端为±1
        result = 2 * range_pos - 1
        result = result.clip(-1, 1)
        return result
