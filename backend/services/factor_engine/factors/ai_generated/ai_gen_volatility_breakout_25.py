"""AI因子: 波动率突变突破 | 置信:60% | 比较短期已实现波动率与长期波动率之比，捕捉波动率突变。当短期波动率显著放大且价格向上突破时，视为有效突破信号；若波动放大但价格未跟随，则视为衰竭。用波动率比值乘以短期收益方向并做标准化。"""
import pandas as pd
import numpy as np
from backend.services.factor_engine.factor_base import BaseFactor, FactorMetadata
from backend.services.factor_engine.factor_registry import register_factor


@register_factor()
class VolatilityRegimeBreakout(BaseFactor):
    """比较短期已实现波动率与长期波动率之比，捕捉波动率突变。当短期波动率显著放大且价格向上突破时，视为有效突破信号；若波动放大但价格未跟随，则视为衰竭。用波动率比值乘以短期收益方向并做标准化。"""

    def get_metadata(self) -> FactorMetadata:
        return FactorMetadata(
            factor_id="ai_gen_volatility_breakout_25",
            name="Volatility Regime Breakout",
            display_name="波动率突变突破",
            description="比较短期已实现波动率与长期波动率之比，捕捉波动率突变。当短期波动率显著放大且价格向上突破时，视为有效突破信号；若波动放大但价格未跟随，则视为衰竭。用波动率比值乘以短期收益方向并做标准化。",
            category="technical",
            subcategory="volatility",
            version="1.0.0-ai",
            author="AI Generated (D7)",
        )

    def calculate(self, data):
        ret = data['close'].pct_change()
        short_vol = ret.rolling(5).std()
        long_vol = ret.rolling(30).std() + 1e-9
        ratio = short_vol / long_vol
        mom = data['close'].pct_change(5)
        result = ((ratio - 1.0) * mom).rolling(3).mean().clip(-1, 1)
        return result
