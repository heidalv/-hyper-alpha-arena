"""AI因子: 插针影线不对称反转 | 置信:62% | 利用上下影线不对称度衡量买卖方防守强度：长下影(买方承接)预示反弹，长上影(卖方拒绝)预示回落。用3日滚动均值平滑噪音，与短期收益方向交互增强反转信号。"""
import pandas as pd
import numpy as np
from backend.services.factor_engine.factor_base import BaseFactor, FactorMetadata
from backend.services.factor_engine.factor_registry import register_factor


@register_factor()
class PinBarAsymmetryReversal(BaseFactor):
    """利用上下影线不对称度衡量买卖方防守强度：长下影(买方承接)预示反弹，长上影(卖方拒绝)预示回落。用3日滚动均值平滑噪音，与短期收益方向交互增强反转信号。"""

    def get_metadata(self) -> FactorMetadata:
        return FactorMetadata(
            factor_id="ai_gen_pin_bar_reversal",
            name="Pin Bar Asymmetry Reversal",
            display_name="插针影线不对称反转",
            description="利用上下影线不对称度衡量买卖方防守强度：长下影(买方承接)预示反弹，长上影(卖方拒绝)预示回落。用3日滚动均值平滑噪音，与短期收益方向交互增强反转信号。",
            category="technical",
            subcategory="contrarian",
            version="1.0.0-ai",
            author="AI Generated (D7)",
        )

    def calculate(self, data):
        body = (data['close'] - data['open']).abs() + 1e-9
        upper = data['high'] - data[['open','close']].max(axis=1)
        lower = data[['open','close']].min(axis=1) - data['low']
        asym = ((lower - upper) / body).rolling(3).mean()
        ret = data['close'].pct_change(3)
        result = (asym - ret * 2).clip(-1, 1)
        return result
