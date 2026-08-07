"""AI因子: 噪声水平 | 置信:60% | 基于效率比（Efficiency Ratio）衡量价格趋势的持续性。效率比低表示市场噪声大，趋势不稳定，容易导致假突破和止损。因子值越高，噪声越大，亏损概率越高。"""
import pandas as pd
import numpy as np
from backend.services.factor_engine.factor_base import BaseFactor, FactorMetadata
from backend.services.factor_engine.factor_registry import register_factor


@register_factor()
class NoiseLevel(BaseFactor):
    """基于效率比（Efficiency Ratio）衡量价格趋势的持续性。效率比低表示市场噪声大，趋势不稳定，容易导致假突破和止损。因子值越高，噪声越大，亏损概率越高。"""

    def get_metadata(self) -> FactorMetadata:
        return FactorMetadata(
            factor_id="ai_gen_noise",
            name="Noise Level",
            display_name="噪声水平",
            description="基于效率比（Efficiency Ratio）衡量价格趋势的持续性。效率比低表示市场噪声大，趋势不稳定，容易导致假突破和止损。因子值越高，噪声越大，亏损概率越高。",
            category="technical",
            subcategory="volatility",
            version="1.0.0-ai",
            author="AI Generated (D7)",
        )

    def calculate(self, data):
        # 计算效率比: |close - close.shift(N)| / sum(|change|)
        N = 14
        price_change = data['close'].diff(N).abs()
        cumulative_vol = data['close'].diff().abs().rolling(N).sum()
        efficiency = price_change / cumulative_vol
        # 噪声因子 = 1 - efficiency, 映射到[-1,1]
        noise = 1 - efficiency
        # 标准化到[-1,1]，去除极端值
        noise = noise.clip(0, 1) * 2 - 1
        return noise.fillna(0)
