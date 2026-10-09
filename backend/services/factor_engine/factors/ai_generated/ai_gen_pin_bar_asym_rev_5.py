"""AI因子: 插针影线不对称反转 | 置信:62% | 利用上下影线不对称度衡量买卖双方防守强度：下影线主导（买方承接）后短期反弹概率高，上影线主导（卖方拒绝）后短期回落概率高。对不对称度做5期滚动均值并反向映射到短期收益方向，形成可测IC的插针反转因子。"""
import pandas as pd
import numpy as np
from backend.services.factor_engine.factor_base import BaseFactor, FactorMetadata
from backend.services.factor_engine.factor_registry import register_factor


@register_factor()
class PinBarAsymmetryReversal5(BaseFactor):
    """利用上下影线不对称度衡量买卖双方防守强度：下影线主导（买方承接）后短期反弹概率高，上影线主导（卖方拒绝）后短期回落概率高。对不对称度做5期滚动均值并反向映射到短期收益方向，形成可测IC的插针反转因子。"""

    def get_metadata(self) -> FactorMetadata:
        return FactorMetadata(
            factor_id="ai_gen_pin_bar_asym_rev_5",
            name="Pin Bar Asymmetry Reversal 5",
            display_name="插针影线不对称反转",
            description="利用上下影线不对称度衡量买卖双方防守强度：下影线主导（买方承接）后短期反弹概率高，上影线主导（卖方拒绝）后短期回落概率高。对不对称度做5期滚动均值并反向映射到短期收益方向，形成可测IC的插针反转因子。",
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
