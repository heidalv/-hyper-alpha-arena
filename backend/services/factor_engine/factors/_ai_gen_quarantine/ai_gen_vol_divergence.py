"""AI因子: 波动率背离因子 | 置信:60% | 通过短期和长期波动率的背离来识别市场状态切换。当短期波动率远高于长期波动率时，市场进入无序状态，容易发生反向运动。使用ATR比率和价格波动幅度，输出负值表示高风险区，正值表示低风险区。"""
import pandas as pd
import numpy as np
from backend.services.factor_engine.factor_base import BaseFactor, FactorMetadata
from backend.services.factor_engine.factor_registry import register_factor


@register_factor()
class VolatilityDivergenceFactor(BaseFactor):
    """通过短期和长期波动率的背离来识别市场状态切换。当短期波动率远高于长期波动率时，市场进入无序状态，容易发生反向运动。使用ATR比率和价格波动幅度，输出负值表示高风险区，正值表示低风险区。"""

    def get_metadata(self) -> FactorMetadata:
        return FactorMetadata(
            factor_id="ai_gen_vol_divergence",
            name="Volatility Divergence Factor",
            display_name="波动率背离因子",
            description="通过短期和长期波动率的背离来识别市场状态切换。当短期波动率远高于长期波动率时，市场进入无序状态，容易发生反向运动。使用ATR比率和价格波动幅度，输出负值表示高风险区，正值表示低风险区。",
            category="technical",
            subcategory="volatility",
            version="1.0.0-ai",
            author="AI Generated (D7)",
        )

    def calculate(self, data):
        import numpy as np
        # 计算ATR
        high, low, close = data['high'], data['low'], data['close']
        tr = np.maximum(high - low, np.maximum(abs(high - close.shift(1)), abs(low - close.shift(1))))
        atr_short = tr.rolling(5).mean()
        atr_long = tr.rolling(20).mean()
        # 波动率背离
        ratio = atr_short / atr_long
        # 归一化到[-1,1]，使用历史分位数或tanh
        median = ratio.rolling(60).median().fillna(1.0)
        std = ratio.rolling(60).std().fillna(0.2)
        z = (ratio - median) / (std + 1e-6)
        result = np.clip(z * -0.3, -1, 1)  # 负值表示高波动背离风险
        return result.rename('ai_gen_vol_divergence')
