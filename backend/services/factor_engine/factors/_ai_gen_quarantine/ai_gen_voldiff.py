"""AI因子: 波动率分化因子 | 置信:60% | 衡量短期波动率相对于长期波动率的异常放大，当短期ATR显著高于长期ATR时，表明市场处于高不确定性状态，容易产生无序波动导致亏损。因子值接近-1表示短期波动率远高于长期，提示风险；接近+1表示短期波动率低于长期，趋势可能稳定。"""
import pandas as pd
import numpy as np
from backend.services.factor_engine.factor_base import BaseFactor, FactorMetadata
from backend.services.factor_engine.factor_registry import register_factor


@register_factor()
class VolatilityDivergence(BaseFactor):
    """衡量短期波动率相对于长期波动率的异常放大，当短期ATR显著高于长期ATR时，表明市场处于高不确定性状态，容易产生无序波动导致亏损。因子值接近-1表示短期波动率远高于长期，提示风险；接近+1表示短期波动率低于长期，趋势可能稳定。"""

    def get_metadata(self) -> FactorMetadata:
        return FactorMetadata(
            factor_id="ai_gen_voldiff",
            name="Volatility Divergence",
            display_name="波动率分化因子",
            description="衡量短期波动率相对于长期波动率的异常放大，当短期ATR显著高于长期ATR时，表明市场处于高不确定性状态，容易产生无序波动导致亏损。因子值接近-1表示短期波动率远高于长期，提示风险；接近+1表示短期波动率低于长期，趋势可能稳定。",
            category="technical",
            subcategory="volatility",
            version="1.0.0-ai",
            author="AI Generated (D7)",
        )

    def calculate(self, data):
        import numpy as np
        tr = np.maximum(data['high'] - data['low'], np.maximum(abs(data['high'] - data['close'].shift(1)), abs(data['low'] - data['close'].shift(1))))
        atr_short = tr.rolling(5).mean()
        atr_long = tr.rolling(20).mean()
        ratio = atr_short / atr_long - 1
        # 归一化到[-1,1]
        result = -2 * (1 / (1 + np.exp(-ratio * 3)) - 0.5)
        return result.fillna(0)
