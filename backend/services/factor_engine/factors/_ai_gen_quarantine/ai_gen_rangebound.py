"""AI因子: 区间震荡效率 | 置信:62% | 基于布林带带宽和价格在带内的位置，结合成交量变化，判断市场是否处于窄幅震荡状态。当带宽收缩且价格频繁穿越中轨时，持仓容易超时或止损，因子值为负；趋势突破时为正。"""
import pandas as pd
import numpy as np
from backend.services.factor_engine.factor_base import BaseFactor, FactorMetadata
from backend.services.factor_engine.factor_registry import register_factor


@register_factor()
class Range_Bound_Efficiency(BaseFactor):
    """基于布林带带宽和价格在带内的位置，结合成交量变化，判断市场是否处于窄幅震荡状态。当带宽收缩且价格频繁穿越中轨时，持仓容易超时或止损，因子值为负；趋势突破时为正。"""

    def get_metadata(self) -> FactorMetadata:
        return FactorMetadata(
            factor_id="ai_gen_rangebound",
            name="Range Bound Efficiency",
            display_name="区间震荡效率",
            description="基于布林带带宽和价格在带内的位置，结合成交量变化，判断市场是否处于窄幅震荡状态。当带宽收缩且价格频繁穿越中轨时，持仓容易超时或止损，因子值为负；趋势突破时为正。",
            category="composite",
            subcategory="volatility",
            version="1.0.0-ai",
            author="AI Generated (D7)",
        )

    def calculate(self, data):
        C = data['close']
        V = data['volume']
        # 布林带
        ma20 = C.rolling(20).mean()
        std20 = C.rolling(20).std()
        upper = ma20 + 2 * std20
        lower = ma20 - 2 * std20
        bandwidth = (upper - lower) / ma20
        # 价格在带内位置（0~1）
        position = (C - lower) / (upper - lower).replace(0, np.nan)
        # 计算位置变化的绝对值和成交量异常
        pos_change = position.diff().abs()
        vol_z = (V - V.rolling(20).mean()) / V.rolling(20).std().replace(0, np.nan)
        # 组合：带宽小、位置变化频繁、成交量平淡 => 震荡
        raw = -bandwidth * 10 + pos_change * 5 - vol_z.abs() * 0.5
        # 滚动分位数映射
        rank = raw.rolling(100, min_periods=20).apply(lambda x: (x.rank(pct=True).iloc[-1] - 0.5) * 2, raw=False)
        return rank.fillna(0).clip(-1, 1)
