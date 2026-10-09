"""AI因子: 插针影线不对称反转 | 置信:62% | 用上下影线不对称度(lower-upper)/body衡量买卖方防守强度，取3期滚动均值并反向，捕捉长下影承接后反弹、长上影拒绝后回落的反转alpha。"""
import pandas as pd
import numpy as np
from backend.services.factor_engine.factor_base import BaseFactor, FactorMetadata
from backend.services.factor_engine.factor_registry import register_factor


@register_factor()
class PinBarSkewReversal(BaseFactor):
    """用上下影线不对称度(lower-upper)/body衡量买卖方防守强度，取3期滚动均值并反向，捕捉长下影承接后反弹、长上影拒绝后回落的反转alpha。"""

    def get_metadata(self) -> FactorMetadata:
        return FactorMetadata(
            factor_id="ai_gen_pin_bar_skew_rev",
            name="Pin Bar Skew Reversal",
            display_name="插针影线不对称反转",
            description="用上下影线不对称度(lower-upper)/body衡量买卖方防守强度，取3期滚动均值并反向，捕捉长下影承接后反弹、长上影拒绝后回落的反转alpha。",
            category="technical",
            subcategory="contrarian",
            version="1.0.0-ai",
            author="AI Generated (D7)",
        )

    def calculate(self, data):
        body = (data['close'] - data['open']).abs() + 1e-9
        upper = data['high'] - data[['open','close']].max(axis=1)
        lower = data[['open','close']].min(axis=1) - data['low']
        skew = (lower - upper) / body
        result = (skew.rolling(3).mean() / (skew.rolling(20).std() + 1e-9)).clip(-1, 1)
        return result
