"""AI因子: 波动率突破 | 置信:60% | 当近期ATR相对历史均值显著升高时，市场进入不稳定状态（regime unknown），多头风险增加。因子值负表示高波动风险，正表示低波动适合做多。"""
import pandas as pd
import numpy as np
from backend.services.factor_engine.factor_base import BaseFactor, FactorMetadata
from backend.services.factor_engine.factor_registry import register_factor


@register_factor()
class Volatility_Breakout(BaseFactor):
    """当近期ATR相对历史均值显著升高时，市场进入不稳定状态（regime unknown），多头风险增加。因子值负表示高波动风险，正表示低波动适合做多。"""

    def get_metadata(self) -> FactorMetadata:
        return FactorMetadata(
            factor_id="ai_gen_vola_break",
            name="Volatility Breakout",
            display_name="波动率突破",
            description="当近期ATR相对历史均值显著升高时，市场进入不稳定状态（regime unknown），多头风险增加。因子值负表示高波动风险，正表示低波动适合做多。",
            category="technical",
            subcategory="volatility",
            version="1.0.0-ai",
            author="AI Generated (D7)",
        )

    def calculate(self, data):
        import numpy as np
        high = data['high']
        low = data['low']
        close = data['close']
        tr = np.maximum(high - low, np.abs(high - close.shift(1)), np.abs(low - close.shift(1)))
        atr = tr.rolling(14).mean()
        atr_ma = atr.rolling(60).mean()
        ratio = atr / atr_ma
        # 映射到[-1,1]，假设ratio在0.5~2之间线性，小于0.5为-1，大于2为1
        result = np.clip((ratio - 0.5) / (2 - 0.5) * 2 - 1, -1, 1)
        result = pd.Series(result, index=data.index).fillna(0)
        return result
