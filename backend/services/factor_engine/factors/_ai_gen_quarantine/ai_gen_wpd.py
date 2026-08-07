"""AI因子: 量价离散度 | 置信:60% | 衡量价格在成交量维度上的离散程度，用于识别市场是否处于无序震荡（未知状态）。通过计算成交量加权的价格分布宽度（类似于VWAP偏离度），当离散度大时价格波动无方向性，容易触发止损。输出[-1,1]：正值表示离散度高（无序），负值表示集中。"""
import pandas as pd
import numpy as np
from backend.services.factor_engine.factor_base import BaseFactor, FactorMetadata
from backend.services.factor_engine.factor_registry import register_factor


@register_factor()
class VolumeWeightedPriceDispersion(BaseFactor):
    """衡量价格在成交量维度上的离散程度，用于识别市场是否处于无序震荡（未知状态）。通过计算成交量加权的价格分布宽度（类似于VWAP偏离度），当离散度大时价格波动无方向性，容易触发止损。输出[-1,1]：正值表示离散度高（无序），负值表示集中。"""

    def get_metadata(self) -> FactorMetadata:
        return FactorMetadata(
            factor_id="ai_gen_wpd",
            name="Volume-Weighted Price Dispersion",
            display_name="量价离散度",
            description="衡量价格在成交量维度上的离散程度，用于识别市场是否处于无序震荡（未知状态）。通过计算成交量加权的价格分布宽度（类似于VWAP偏离度），当离散度大时价格波动无方向性，容易触发止损。输出[-1,1]：正值表示离散度高（无序），负值表示集中。",
            category="behavioral",
            subcategory="volume",
            version="1.0.0-ai",
            author="AI Generated (D7)",
        )

    def calculate(self, data):
        # 使用成交量和价格计算vwap
        typical_price = (data['high'] + data['low'] + data['close']) / 3
        vwap = (data['volume'] * typical_price).rolling(window=14, min_periods=1).sum() / data['volume'].rolling(window=14, min_periods=1).sum()
        # 价格相对于vwap的偏离（百分比）
        pct_dev = (data['close'] - vwap) / vwap
        # 离散度：近5期偏离的绝对值的滚动均值
        abs_dev = np.abs(pct_dev).rolling(window=5, min_periods=1).mean()
        # 最大离散度假设为0.02 (2%)，映射到[-1,1]；取相反数使得正值对应高离散
        normalized = np.clip(abs_dev / 0.02, 0, 1)
        return pd.Series(normalized * 2 - 1, index=data.index)
