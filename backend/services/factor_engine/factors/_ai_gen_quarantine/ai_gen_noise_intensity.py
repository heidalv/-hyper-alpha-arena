"""AI因子: 噪声强度 | 置信:55% | 通过上下影线长度与实体比例，结合成交量偏离，量化市场微小噪音干扰。计算每根K线的上影线=(high-max(open,close))，下影线=(min(open,close)-low)，实体=abs(close-open)。当(上影线+下影线)/实体>2且成交量高于20日均值1.5倍时，视为强噪声，输出负值。"""
import pandas as pd
import numpy as np
from backend.services.factor_engine.factor_base import BaseFactor, FactorMetadata
from backend.services.factor_engine.factor_registry import register_factor


@register_factor()
class NoiseIntensity(BaseFactor):
    """通过上下影线长度与实体比例，结合成交量偏离，量化市场微小噪音干扰。计算每根K线的上影线=(high-max(open,close))，下影线=(min(open,close)-low)，实体=abs(close-open)。当(上影线+下影线)/实体>2且成交量高于20日均值1.5倍时，视为强噪声，输出负值。"""

    def get_metadata(self) -> FactorMetadata:
        return FactorMetadata(
            factor_id="ai_gen_noise_intensity",
            name="Noise Intensity",
            display_name="噪声强度",
            description="通过上下影线长度与实体比例，结合成交量偏离，量化市场微小噪音干扰。计算每根K线的上影线=(high-max(open,close))，下影线=(min(open,close)-low)，实体=abs(close-open)。当(上影线+下影线)/实体>2且成交量高于20日均值1.5倍时，视为强噪声，输出负值。",
            category="technical",
            subcategory="volatility",
            version="1.0.0-ai",
            author="AI Generated (D7)",
        )

    def calculate(self, data):
        body = (data['close'] - data['open']).abs()
        upper_shadow = data['high'] - data[['open','close']].max(axis=1)
        lower_shadow = data[['open','close']].min(axis=1) - data['low']
        total_shadow = upper_shadow + lower_shadow
        # 防止除以0
        ratio = total_shadow / (body + 1e-8)
        # 成交量均值
        vol_avg = data['volume'].rolling(20).mean()
        vol_ratio = data['volume'] / (vol_avg + 1e-8)
        # 噪声条件：影线比例>2且成交量>1.5倍均值
        noise_cond = (ratio > 2.0) & (vol_ratio > 1.5)
        result = -1.0 * noise_cond.astype(float)
        result = result.rolling(2).mean().fillna(0).clip(-1,1)
        return result
