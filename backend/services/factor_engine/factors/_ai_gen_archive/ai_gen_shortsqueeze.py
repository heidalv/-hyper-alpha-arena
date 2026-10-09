"""AI因子: 空头挤压压力 | 置信:55% | 结合成交量放大和价格下行受阻，识别空头可能被挤压的形态，用于避免做空后反弹亏损。"""
import pandas as pd
import numpy as np
from backend.services.factor_engine.factor_base import BaseFactor, FactorMetadata
from backend.services.factor_engine.factor_registry import register_factor


@register_factor()
class ShortSqueezePressure(BaseFactor):
    """结合成交量放大和价格下行受阻，识别空头可能被挤压的形态，用于避免做空后反弹亏损。"""

    def get_metadata(self) -> FactorMetadata:
        return FactorMetadata(
            factor_id="ai_gen_shortsqueeze",
            name="Short Squeeze Pressure",
            display_name="空头挤压压力",
            description="结合成交量放大和价格下行受阻，识别空头可能被挤压的形态，用于避免做空后反弹亏损。",
            category="composite",
            subcategory="volume",
            version="1.0.0-ai",
            author="AI Generated (D7)",
        )

    def calculate(self, data):
        ret = data['close'].pct_change(5)
        vol_ratio = data['volume'] / data['volume'].rolling(20).mean()
        downside_pressure = (data['close'] - data['low'].rolling(5).min()) / (data['high'].rolling(5).max() - data['low'].rolling(5).min() + 1e-9)
        result = -1 * (vol_ratio * (1 - downside_pressure) * (1 - ret)).clip(-1, 1)
        return result
