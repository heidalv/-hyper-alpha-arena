"""AI因子: 动量衰减反转 | 置信:60% | 利用累计收益率与当前收益率的交互作用，识别趋势衰竭后的反转点。当过去一段时间累计上涨且当前出现下跌时，预示多头离场；累计下跌且当前反弹时，预示空头回补。通过乘积取反并双曲正切归一化至[-1,1]。"""
import pandas as pd
import numpy as np
from backend.services.factor_engine.factor_base import BaseFactor, FactorMetadata
from backend.services.factor_engine.factor_registry import register_factor


@register_factor()
class MomentumDecayReversal(BaseFactor):
    """利用累计收益率与当前收益率的交互作用，识别趋势衰竭后的反转点。当过去一段时间累计上涨且当前出现下跌时，预示多头离场；累计下跌且当前反弹时，预示空头回补。通过乘积取反并双曲正切归一化至[-1,1]。"""

    def get_metadata(self) -> FactorMetadata:
        return FactorMetadata(
            factor_id="ai_gen_mdr",
            name="Momentum Decay Reversal",
            display_name="动量衰减反转",
            description="利用累计收益率与当前收益率的交互作用，识别趋势衰竭后的反转点。当过去一段时间累计上涨且当前出现下跌时，预示多头离场；累计下跌且当前反弹时，预示空头回补。通过乘积取反并双曲正切归一化至[-1,1]。",
            category="composite",
            subcategory="momentum",
            version="1.0.0-ai",
            author="AI Generated (D7)",
        )

    def calculate(self, data):
        import numpy as np
        close = data['close']
        ret = close.pct_change().fillna(0)
        sum_ret = ret.rolling(10).sum().fillna(0)
        raw = - sum_ret * ret
        result = pd.Series(np.tanh(raw), index=data.index)
        return result
