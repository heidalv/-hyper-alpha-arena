"""AI因子: 插针下影反转5 | 置信:62% | 长下影线代表买方在低位承接，属于短线超卖反转信号。用下影与上影的不对称度（(lower-upper)/body）的短期均值衡量买方防守强度，正值越大预示后续反弹概率越高，负值（上影主导）预示回落。"""
import pandas as pd
import numpy as np
from backend.services.factor_engine.factor_base import BaseFactor, FactorMetadata
from backend.services.factor_engine.factor_registry import register_factor


@register_factor()
class PinbarLowerShadowReversal5(BaseFactor):
    """长下影线代表买方在低位承接，属于短线超卖反转信号。用下影与上影的不对称度（(lower-upper)/body）的短期均值衡量买方防守强度，正值越大预示后续反弹概率越高，负值（上影主导）预示回落。"""

    def get_metadata(self) -> FactorMetadata:
        return FactorMetadata(
            factor_id="ai_gen_pinbar_reversal_5",
            name="Pinbar Lower Shadow Reversal 5",
            display_name="插针下影反转5",
            description="长下影线代表买方在低位承接，属于短线超卖反转信号。用下影与上影的不对称度（(lower-upper)/body）的短期均值衡量买方防守强度，正值越大预示后续反弹概率越高，负值（上影主导）预示回落。",
            category="technical",
            subcategory="mean_reversion",
            version="1.0.0-ai",
            author="AI Generated (D7)",
        )

    def calculate(self, data):
        body = (data['close'] - data['open']).abs() + 1e-9
        upper = data['high'] - data[['open','close']].max(axis=1)
        lower = data[['open','close']].min(axis=1) - data['low']
        result = ((lower - upper) / body).rolling(5).mean().clip(-1, 1)
        return result
