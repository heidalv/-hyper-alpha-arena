"""AI因子: 插针不对称反转 | 置信:62% | 长下影线（买方防守）后短期反弹、长上影线（卖方拒绝）后回落。用影线不对称度(lower-upper)/body 的滚动均值捕捉插针反转方向，值域[-1,1]，正值看多。"""
import pandas as pd
import numpy as np
from backend.services.factor_engine.factor_base import BaseFactor, FactorMetadata
from backend.services.factor_engine.factor_registry import register_factor


@register_factor()
class PinBarAsymmetryReversal(BaseFactor):
    """长下影线（买方防守）后短期反弹、长上影线（卖方拒绝）后回落。用影线不对称度(lower-upper)/body 的滚动均值捕捉插针反转方向，值域[-1,1]，正值看多。"""

    def get_metadata(self) -> FactorMetadata:
        return FactorMetadata(
            factor_id="ai_gen_pin_reversal_asym",
            name="Pin Bar Asymmetry Reversal",
            display_name="插针不对称反转",
            description="长下影线（买方防守）后短期反弹、长上影线（卖方拒绝）后回落。用影线不对称度(lower-upper)/body 的滚动均值捕捉插针反转方向，值域[-1,1]，正值看多。",
            category="technical",
            subcategory="contrarian",
            version="1.0.0-ai",
            author="AI Generated (D7)",
        )

    def calculate(self, data):
        body = (data['close'] - data['open']).abs() + 1e-9
        upper = data['high'] - data[['open','close']].max(axis=1)
        lower = data[['open','close']].min(axis=1) - data['low']
        result = ((lower - upper) / body).rolling(3).mean().clip(-1, 1)
        return result
