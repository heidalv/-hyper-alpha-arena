"""AI因子: 波动调整反转强度 | 置信:55% | 在未知市场状态中，高波动常伴随短期反转。通过比较当前收盘价与过去N周期均值，并用波动率标准化，捕捉极端波动后的反转信号。"""
import pandas as pd
import numpy as np
from backend.services.factor_engine.factor_base import BaseFactor, FactorMetadata
from backend.services.factor_engine.factor_registry import register_factor


@register_factor()
class VolatilityAdjustedReversalIntensity(BaseFactor):
    """在未知市场状态中，高波动常伴随短期反转。通过比较当前收盘价与过去N周期均值，并用波动率标准化，捕捉极端波动后的反转信号。"""

    def get_metadata(self) -> FactorMetadata:
        return FactorMetadata(
            factor_id="ai_gen_rev_vol",
            name="Volatility-Adjusted Reversal Intensity",
            display_name="波动调整反转强度",
            description="在未知市场状态中，高波动常伴随短期反转。通过比较当前收盘价与过去N周期均值，并用波动率标准化，捕捉极端波动后的反转信号。",
            category="behavioral",
            subcategory="mean_reversion",
            version="1.0.0-ai",
            author="AI Generated (D7)",
        )

    def calculate(self, data):
        import numpy as np
        close = data['close']
        high = data['high']
        low = data['low']

        # 1. 计算波动率（ATR）
        tr = np.maximum(high - low, np.abs(high - close.shift(1)), np.abs(low - close.shift(1)))
        atr = tr.rolling(14).mean()

        # 2. 计算价格相对20日均线的偏离
        ma20 = close.rolling(20).mean()
        deviation = (close - ma20) / atr

        # 3. 反转强度：在偏差绝对值大于2个ATR时，倾向于反向
        threshold = 2.0
        rev_signal = -np.sign(deviation) * np.where(np.abs(deviation) > threshold, 1.0, 0.0)

        # 4. 平滑并归一化到[-1,1]
        result = rev_signal.rolling(3).mean().fillna(0)
        return result.clip(-1, 1)
