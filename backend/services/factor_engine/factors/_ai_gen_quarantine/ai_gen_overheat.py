"""AI因子: 超买过热因子 | 置信:60% | 当RSI超过70且成交量均值下降（过去5日成交量低于过去20日均值80%）时，表明上涨动能衰竭，做多风险高，输出负值；否则输出正值。"""
import pandas as pd
import numpy as np
from backend.services.factor_engine.factor_base import BaseFactor, FactorMetadata
from backend.services.factor_engine.factor_registry import register_factor


@register_factor()
class Overheat_Short_Signal(BaseFactor):
    """当RSI超过70且成交量均值下降（过去5日成交量低于过去20日均值80%）时，表明上涨动能衰竭，做多风险高，输出负值；否则输出正值。"""

    def get_metadata(self) -> FactorMetadata:
        return FactorMetadata(
            factor_id="ai_gen_overheat",
            name="Overheat Short Signal",
            display_name="超买过热因子",
            description="当RSI超过70且成交量均值下降（过去5日成交量低于过去20日均值80%）时，表明上涨动能衰竭，做多风险高，输出负值；否则输出正值。",
            category="behavioral",
            subcategory="mean_reversion",
            version="1.0.0-ai",
            author="AI Generated (D7)",
        )

    def calculate(self, data):
        import pandas as pd
        import numpy as np
        close = data['close']
        volume = data['volume']
        # RSI
        delta = close.diff()
        gain = delta.clip(lower=0)
        loss = (-delta).clip(lower=0)
        avg_gain = gain.rolling(14).mean()
        avg_loss = loss.rolling(14).mean()
        rs = avg_gain / (avg_loss + 1e-10)
        rsi = 100 - 100 / (1 + rs)
        # 成交量萎缩条件
        vol_5 = volume.rolling(5).mean()
        vol_20 = volume.rolling(20).mean()
        cond_vol = vol_5 < 0.8 * vol_20
        # 超买且成交量萎缩
        condition = (rsi > 70) & cond_vol
        result = pd.Series(np.where(condition, -1.0, 1.0), index=data.index)
        return result
