"""AI因子: 插针影线不对称反转 | 置信:62% | 用下影线与上影线的不对称度衡量买卖方防守强度，结合短期收益方向做反转：长下影主导(买方承接)后短期反弹概率高，长上影主导(卖方拒绝)后短期回落概率高。取3期滚动均值平滑噪声，输出[-1,1]。"""
import pandas as pd
import numpy as np
from backend.services.factor_engine.factor_base import BaseFactor, FactorMetadata
from backend.services.factor_engine.factor_registry import register_factor


@register_factor()
class PinBarAsymmetryReversal(BaseFactor):
    """用下影线与上影线的不对称度衡量买卖方防守强度，结合短期收益方向做反转：长下影主导(买方承接)后短期反弹概率高，长上影主导(卖方拒绝)后短期回落概率高。取3期滚动均值平滑噪声，输出[-1,1]。"""

    def get_metadata(self) -> FactorMetadata:
        return FactorMetadata(
            factor_id="ai_gen_pin_bar_reversal_5",
            name="Pin Bar Asymmetry Reversal",
            display_name="插针影线不对称反转",
            description="用下影线与上影线的不对称度衡量买卖方防守强度，结合短期收益方向做反转：长下影主导(买方承接)后短期反弹概率高，长上影主导(卖方拒绝)后短期回落概率高。取3期滚动均值平滑噪声，输出[-1,1]。",
            category="technical",
            subcategory="contrarian",
            version="1.0.0-ai",
            author="AI Generated (D7)",
        )

    def calculate(self, data):
        body = (data['close'] - data['open']).abs() + 1e-9
        upper = data['high'] - data[['open','close']].max(axis=1)
        lower = data[['open','close']].min(axis=1) - data['low']
        asym = (lower - upper) / body
        ret = data['close'].pct_change(3)
        result = (asym.rolling(3).mean() * (1 - ret.rolling(3).mean().rank(pct=True) * 2)).clip(-1, 1)
        return result
