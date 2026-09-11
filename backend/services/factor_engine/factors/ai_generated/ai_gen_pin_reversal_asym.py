"""AI因子: 插针影线不对称反转 | 置信:60% | 用上下影线不对称度衡量买卖方防守强度：下影主导(买方承接)后短期反弹，上影主导(卖方拒绝)后回落。对不对称度做短期均值并与近期收益方向交互，捕捉插针反转alpha。"""
import pandas as pd
import numpy as np
from backend.services.factor_engine.factor_base import BaseFactor, FactorMetadata
from backend.services.factor_engine.factor_registry import register_factor


@register_factor()
class PinBarAsymmetryReversal(BaseFactor):
    """用上下影线不对称度衡量买卖方防守强度：下影主导(买方承接)后短期反弹，上影主导(卖方拒绝)后回落。对不对称度做短期均值并与近期收益方向交互，捕捉插针反转alpha。"""

    def get_metadata(self) -> FactorMetadata:
        return FactorMetadata(
            factor_id="ai_gen_pin_reversal_asym",
            name="Pin Bar Asymmetry Reversal",
            display_name="插针影线不对称反转",
            description="用上下影线不对称度衡量买卖方防守强度：下影主导(买方承接)后短期反弹，上影主导(卖方拒绝)后回落。对不对称度做短期均值并与近期收益方向交互，捕捉插针反转alpha。",
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
        ret = data['close'].pct_change(3)
        result = (asym.rolling(5).mean() * (-ret).apply(lambda x: 1 if x > 0 else -1)).clip(-1, 1)
        return result
