"""AI因子: 插针反转不对称度20日 | 置信:60% | 用下影线与上影线的不对称度衡量买卖方防守强度：下影主导(买方承接)预示反弹，上影主导(卖方拒绝)预示回落。对不对称度做20日滚动均值并叠加短期收益方向交互，捕捉插针后的均值回归alpha。"""
import pandas as pd
import numpy as np
from backend.services.factor_engine.factor_base import BaseFactor, FactorMetadata
from backend.services.factor_engine.factor_registry import register_factor


@register_factor()
class PinbarReversalAsymmetry20D(BaseFactor):
    """用下影线与上影线的不对称度衡量买卖方防守强度：下影主导(买方承接)预示反弹，上影主导(卖方拒绝)预示回落。对不对称度做20日滚动均值并叠加短期收益方向交互，捕捉插针后的均值回归alpha。"""

    def get_metadata(self) -> FactorMetadata:
        return FactorMetadata(
            factor_id="ai_gen_pinbar_reversal_20d",
            name="Pinbar Reversal Asymmetry 20D",
            display_name="插针反转不对称度20日",
            description="用下影线与上影线的不对称度衡量买卖方防守强度：下影主导(买方承接)预示反弹，上影主导(卖方拒绝)预示回落。对不对称度做20日滚动均值并叠加短期收益方向交互，捕捉插针后的均值回归alpha。",
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
        rev = data['close'].pct_change(3)
        result = (asym.rolling(20).mean() * (-rev).rank(pct=True)).clip(-1, 1)
        return result
