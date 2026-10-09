"""AI因子: 插针密度加权反转 | 置信:58% | 以插针密度（max(upper,lower)/body 的20期均值）作为波动环境权重，与影线不对称度交互。高插针密度环境下影线信号更可靠，放大反转alpha；低密度环境则压缩信号，减少震荡噪声。"""
import pandas as pd
import numpy as np
from backend.services.factor_engine.factor_base import BaseFactor, FactorMetadata
from backend.services.factor_engine.factor_registry import register_factor


@register_factor()
class PinDensityWeightedReversal(BaseFactor):
    """以插针密度（max(upper,lower)/body 的20期均值）作为波动环境权重，与影线不对称度交互。高插针密度环境下影线信号更可靠，放大反转alpha；低密度环境则压缩信号，减少震荡噪声。"""

    def get_metadata(self) -> FactorMetadata:
        return FactorMetadata(
            factor_id="ai_gen_pin_density_weighted_rev",
            name="Pin Density Weighted Reversal",
            display_name="插针密度加权反转",
            description="以插针密度（max(upper,lower)/body 的20期均值）作为波动环境权重，与影线不对称度交互。高插针密度环境下影线信号更可靠，放大反转alpha；低密度环境则压缩信号，减少震荡噪声。",
            category="technical",
            subcategory="volatility",
            version="1.0.0-ai",
            author="AI Generated (D7)",
        )

    def calculate(self, data):
        body = (data['close'] - data['open']).abs() + 1e-9
        upper = data['high'] - data[['open','close']].max(axis=1)
        lower = data[['open','close']].min(axis=1) - data['low']
        asym = (lower - upper) / body
        density = (data[['open','close']].max(axis=1) - data[['open','close']].min(axis=1)).rolling(20).mean()
        result = (asym * (1 + density)).rolling(5).mean().clip(-1, 1)
        return result
