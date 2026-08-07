"""AI因子: 波动率收缩因子 | 置信:65% | 通过比较短期ATR与长期ATR的比率，识别市场波动率显著收缩的状态。当比率低于阈值时，表明市场进入低波动未知区间，做多容易亏损，因子输出负值。"""
import pandas as pd
import numpy as np
from backend.services.factor_engine.factor_base import BaseFactor, FactorMetadata
from backend.services.factor_engine.factor_registry import register_factor


@register_factor()
class Volatility_Contraction_Factor(BaseFactor):
    """通过比较短期ATR与长期ATR的比率，识别市场波动率显著收缩的状态。当比率低于阈值时，表明市场进入低波动未知区间，做多容易亏损，因子输出负值。"""

    def get_metadata(self) -> FactorMetadata:
        return FactorMetadata(
            factor_id="ai_gen_vol_cont",
            name="Volatility Contraction Factor",
            display_name="波动率收缩因子",
            description="通过比较短期ATR与长期ATR的比率，识别市场波动率显著收缩的状态。当比率低于阈值时，表明市场进入低波动未知区间，做多容易亏损，因子输出负值。",
            category="technical",
            subcategory="volatility",
            version="1.0.0-ai",
            author="AI Generated (D7)",
        )

    def calculate(self, data):
        import pandas as pd
        import numpy as np
        length = len(data)
        if length < 60:
            return pd.Series(np.nan, index=data.index)
        atr_short = data['high'].rolling(20).max() - data['low'].rolling(20).min()
        atr_long = data['high'].rolling(60).max() - data['low'].rolling(60).min()
        ratio = atr_short / atr_long
        ratio = ratio.clip(0.3, 1.5)
        result = -2 * (ratio - 0.7) / (1.5 - 0.3) + 1  # 映射到[-1,1]，0.7处为0
        result = result.clip(-1, 1)
        return result.fillna(0)
