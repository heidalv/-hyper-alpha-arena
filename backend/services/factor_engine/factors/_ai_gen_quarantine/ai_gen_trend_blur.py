"""AI因子: 趋势模糊因子 | 置信:50% | 通过计算收盘价与短期均线（如10周期）的相对位置及近期穿越次数，衡量市场趋势的明确程度。当价格频繁穿越均线且偏离度小时，表示趋势模糊，因子为负值；当价格持续处于均线一侧且偏离度大时，正值。用于规避在regime=unknown下的无方向震荡行情。"""
import pandas as pd
import numpy as np
from backend.services.factor_engine.factor_base import BaseFactor, FactorMetadata
from backend.services.factor_engine.factor_registry import register_factor


@register_factor()
class TrendAmbiguityFactor(BaseFactor):
    """通过计算收盘价与短期均线（如10周期）的相对位置及近期穿越次数，衡量市场趋势的明确程度。当价格频繁穿越均线且偏离度小时，表示趋势模糊，因子为负值；当价格持续处于均线一侧且偏离度大时，正值。用于规避在regime=unknown下的无方向震荡行情。"""

    def get_metadata(self) -> FactorMetadata:
        return FactorMetadata(
            factor_id="ai_gen_trend_blur",
            name="Trend Ambiguity Factor",
            display_name="趋势模糊因子",
            description="通过计算收盘价与短期均线（如10周期）的相对位置及近期穿越次数，衡量市场趋势的明确程度。当价格频繁穿越均线且偏离度小时，表示趋势模糊，因子为负值；当价格持续处于均线一侧且偏离度大时，正值。用于规避在regime=unknown下的无方向震荡行情。",
            category="composite",
            subcategory="mean_reversion",
            version="1.0.0-ai",
            author="AI Generated (D7)",
        )

    def calculate(self, data):
        close = data['close']
        ma = close.rolling(10).mean()
        deviation = (close - ma) / close
        # 计算过去20个周期内价格穿越均线的次数
        cross = ((close.shift(1) - ma.shift(1)) * (close - ma) < 0).astype(int).rolling(20).sum()
        # 模糊程度：穿越次数多且偏离小 => 负值
        raw = -cross / 20 * (1 - abs(deviation).rolling(20).mean())
        # 归一化到[-1,1]
        return raw.clip(-1, 1)
