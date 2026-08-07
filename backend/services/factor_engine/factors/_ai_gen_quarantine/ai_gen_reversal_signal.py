"""AI因子: 反转信号因子 | 置信:65% | 捕获超买后的反转：当RSI超过70且出现长上影线或阴线，预示价格可能反转下跌。因子值为负表示看空信号。"""
import pandas as pd
import numpy as np
from backend.services.factor_engine.factor_base import BaseFactor, FactorMetadata
from backend.services.factor_engine.factor_registry import register_factor


@register_factor()
class Reversal_Signal_Factor(BaseFactor):
    """捕获超买后的反转：当RSI超过70且出现长上影线或阴线，预示价格可能反转下跌。因子值为负表示看空信号。"""

    def get_metadata(self) -> FactorMetadata:
        return FactorMetadata(
            factor_id="ai_gen_reversal_signal",
            name="Reversal Signal Factor",
            display_name="反转信号因子",
            description="捕获超买后的反转：当RSI超过70且出现长上影线或阴线，预示价格可能反转下跌。因子值为负表示看空信号。",
            category="technical",
            subcategory="contrarian",
            version="1.0.0-ai",
            author="AI Generated (D7)",
        )

    def calculate(self, data):
        import pandas as pd
        import numpy as np
        # RSI 14
        delta = data['close'].diff()
        gain = delta.clip(lower=0)
        loss = -delta.clip(upper=0)
        avg_gain = gain.rolling(14).mean()
        avg_loss = loss.rolling(14).mean()
        rs = avg_gain / avg_loss
        rsi = 100 - (100 / (1 + rs))
        # 上影线长度比例
        upper_shadow = data['high'] - data[['open','close']].max(axis=1)
        body = (data['close'] - data['open']).abs()
        shadow_ratio = upper_shadow / (data['high'] - data['low'] + 1e-10)
        # 条件：RSI>70 且 (上影线比例>0.6 或 收盘<开盘)
        cond = (rsi > 70) & ((shadow_ratio > 0.6) | (data['close'] < data['open']))
        raw = cond.astype(float) * -1  # 满足条件赋-1，否则0
        # 平滑并映射到[-1,1]
        result = raw.rolling(3).mean().fillna(0)
        return result
