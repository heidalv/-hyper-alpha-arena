"""AI因子: 插针不对称反转5 | 置信:62% | 计算下影线与上影线的不对称度（下影主导=买方防守），用5期滚动均值平滑后取负号，捕捉长下影承接后的短期反弹与长上影拒绝后的回落，属于短线插针反转alpha。"""
import pandas as pd
import numpy as np
from backend.services.factor_engine.factor_base import BaseFactor, FactorMetadata
from backend.services.factor_engine.factor_registry import register_factor


@register_factor()
class PinBarAsymmetryReversal5(BaseFactor):
    """计算下影线与上影线的不对称度（下影主导=买方防守），用5期滚动均值平滑后取负号，捕捉长下影承接后的短期反弹与长上影拒绝后的回落，属于短线插针反转alpha。"""

    def get_metadata(self) -> FactorMetadata:
        return FactorMetadata(
            factor_id="ai_gen_pinbar_asym_rev_5",
            name="Pin Bar Asymmetry Reversal 5",
            display_name="插针不对称反转5",
            description="计算下影线与上影线的不对称度（下影主导=买方防守），用5期滚动均值平滑后取负号，捕捉长下影承接后的短期反弹与长上影拒绝后的回落，属于短线插针反转alpha。",
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
        result = (-asym.rolling(5).mean()).clip(-1, 1)
        return result
