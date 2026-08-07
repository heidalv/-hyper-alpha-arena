"""AI因子: 波动率突变预警 | 置信:60% | 计算当前波动率（最高-最低/前收盘）与过去N周期滚动均值的比值，当比值超过阈值且价格方向不明确（如近期涨跌幅绝对值较小）时输出负值，表示市场可能进入假突破或止损密集区。"""
import pandas as pd
import numpy as np
from backend.services.factor_engine.factor_base import BaseFactor, FactorMetadata
from backend.services.factor_engine.factor_registry import register_factor


@register_factor()
class VolatilityBreakoutModel(BaseFactor):
    """计算当前波动率（最高-最低/前收盘）与过去N周期滚动均值的比值，当比值超过阈值且价格方向不明确（如近期涨跌幅绝对值较小）时输出负值，表示市场可能进入假突破或止损密集区。"""

    def get_metadata(self) -> FactorMetadata:
        return FactorMetadata(
            factor_id="ai_gen_vbm",
            name="Volatility Breakout Model",
            display_name="波动率突变预警",
            description="计算当前波动率（最高-最低/前收盘）与过去N周期滚动均值的比值，当比值超过阈值且价格方向不明确（如近期涨跌幅绝对值较小）时输出负值，表示市场可能进入假突破或止损密集区。",
            category="technical",
            subcategory="volatility",
            version="1.0.0-ai",
            author="AI Generated (D7)",
        )

    def calculate(self, data):
        n = 20
        high = data['high']
        low = data['low']
        close = data['close']
        vol_range = (high - low) / close.shift(1)
        vol_ma = vol_range.rolling(n).mean()
        vol_ratio = vol_range / vol_ma
        price_move = abs(close.pct_change())
        # 波动率放大但价格窄幅震荡时发出警告
        cond = (vol_ratio > 1.5) & (price_move < price_move.rolling(n).mean() * 0.5)
        result = -cond.astype(float) * 1.0
        result.fillna(0.0, inplace=True)
        return result
