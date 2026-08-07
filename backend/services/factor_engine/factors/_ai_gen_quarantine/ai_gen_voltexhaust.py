"""AI因子: 波动率衰竭因子 | 置信:50% | 识别高波动后成交量萎缩的衰竭形态，常见于反转前的陷阱。当波动率处于高位但成交量相对缩小，且价格未能有效突破，为做多风险信号。计算过去10日波动率百分位与过去5日成交量变化率的乘积，取负值表示风险增大。"""
import pandas as pd
import numpy as np
from backend.services.factor_engine.factor_base import BaseFactor, FactorMetadata
from backend.services.factor_engine.factor_registry import register_factor


@register_factor()
class VolatilityExhaustionFactor(BaseFactor):
    """识别高波动后成交量萎缩的衰竭形态，常见于反转前的陷阱。当波动率处于高位但成交量相对缩小，且价格未能有效突破，为做多风险信号。计算过去10日波动率百分位与过去5日成交量变化率的乘积，取负值表示风险增大。"""

    def get_metadata(self) -> FactorMetadata:
        return FactorMetadata(
            factor_id="ai_gen_voltexhaust",
            name="Volatility Exhaustion Factor",
            display_name="波动率衰竭因子",
            description="识别高波动后成交量萎缩的衰竭形态，常见于反转前的陷阱。当波动率处于高位但成交量相对缩小，且价格未能有效突破，为做多风险信号。计算过去10日波动率百分位与过去5日成交量变化率的乘积，取负值表示风险增大。",
            category="behavioral",
            subcategory="volatility",
            version="1.0.0-ai",
            author="AI Generated (D7)",
        )

    def calculate(self, data):
        close = data['close']
        volume = data['volume']
        vol10 = close.pct_change().rolling(10).std()
        vol_rank = vol10.rank(pct=True)
        vol_chg5 = volume.pct_change(5)
        raw = -vol_rank * (vol_chg5 + 1)
        result = (raw - raw.rolling(20).min()) / (raw.rolling(20).max() - raw.rolling(20).min() + 1e-8) * 2 - 1
        return result.fillna(0)
