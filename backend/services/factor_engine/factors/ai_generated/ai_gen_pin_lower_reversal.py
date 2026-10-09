"""AI因子: 下影线承接反转 | 置信:62% | 长下影线代表买方在低位承接，下影主导时未来短期反弹概率更高；用下影与上影的不对称度做短期反转信号。"""
import pandas as pd
import numpy as np
from backend.services.factor_engine.factor_base import BaseFactor, FactorMetadata
from backend.services.factor_engine.factor_registry import register_factor


@register_factor()
class PinBarLowerShadowReversal(BaseFactor):
    """长下影线代表买方在低位承接，下影主导时未来短期反弹概率更高；用下影与上影的不对称度做短期反转信号。"""

    def get_metadata(self) -> FactorMetadata:
        return FactorMetadata(
            factor_id="ai_gen_pin_lower_reversal",
            name="Pin Bar Lower Shadow Reversal",
            display_name="下影线承接反转",
            description="长下影线代表买方在低位承接，下影主导时未来短期反弹概率更高；用下影与上影的不对称度做短期反转信号。",
            category="technical",
            subcategory="mean_reversion",
            version="1.0.0-ai",
            author="AI Generated (D7)",
        )

    def calculate(self, data):
        body = (data['close'] - data['open']).abs() + 1e-9
        upper = data['high'] - data[['open','close']].max(axis=1)
        lower = data[['open','close']].min(axis=1) - data['low']
        asym = (lower - upper) / body
        result = asym.rolling(3).mean().clip(-1, 1)
        return result
