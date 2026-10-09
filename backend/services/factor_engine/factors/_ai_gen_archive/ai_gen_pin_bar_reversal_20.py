"""AI因子: 插针不对称反转 | 置信:62% | 用上下影线不对称度衡量买卖方防守强度：下影主导(买方承接)且近期收益偏弱时反弹概率高，上影主导(卖方拒绝)且近期收益偏强时回落概率高。因子值 = 不对称度rolling均值 × 短期收益反向，捕捉插针后的均值回归alpha。"""
import pandas as pd
import numpy as np
from backend.services.factor_engine.factor_base import BaseFactor, FactorMetadata
from backend.services.factor_engine.factor_registry import register_factor


@register_factor()
class PinBarAsymmetryReversal(BaseFactor):
    """用上下影线不对称度衡量买卖方防守强度：下影主导(买方承接)且近期收益偏弱时反弹概率高，上影主导(卖方拒绝)且近期收益偏强时回落概率高。因子值 = 不对称度rolling均值 × 短期收益反向，捕捉插针后的均值回归alpha。"""

    def get_metadata(self) -> FactorMetadata:
        return FactorMetadata(
            factor_id="ai_gen_pin_bar_reversal_20",
            name="Pin Bar Asymmetry Reversal",
            display_name="插针不对称反转",
            description="用上下影线不对称度衡量买卖方防守强度：下影主导(买方承接)且近期收益偏弱时反弹概率高，上影主导(卖方拒绝)且近期收益偏强时回落概率高。因子值 = 不对称度rolling均值 × 短期收益反向，捕捉插针后的均值回归alpha。",
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
        ret5 = data['close'].pct_change(5)
        vol = data['close'].pct_change().rolling(20).std() + 1e-9
        result = (asym.rolling(5).mean() * (-ret5 / vol)).clip(-1, 1)
        return result
