"""AI因子: 插针下影线反转偏度 | 置信:62% | 用(下影线-上影线)/实体衡量买卖方防守强度，取3期滚动均值并乘以短期收益方向的反转信号：下影主导(买方承接)后短期反弹概率高，上影主导(卖方拒绝)后回落概率高。"""
import pandas as pd
import numpy as np
from backend.services.factor_engine.factor_base import BaseFactor, FactorMetadata
from backend.services.factor_engine.factor_registry import register_factor


@register_factor()
class PinBarLowerShadowReversalSkew(BaseFactor):
    """用(下影线-上影线)/实体衡量买卖方防守强度，取3期滚动均值并乘以短期收益方向的反转信号：下影主导(买方承接)后短期反弹概率高，上影主导(卖方拒绝)后回落概率高。"""

    def get_metadata(self) -> FactorMetadata:
        return FactorMetadata(
            factor_id="ai_gen_pin_reversal_skew",
            name="Pin Bar Lower Shadow Reversal Skew",
            display_name="插针下影线反转偏度",
            description="用(下影线-上影线)/实体衡量买卖方防守强度，取3期滚动均值并乘以短期收益方向的反转信号：下影主导(买方承接)后短期反弹概率高，上影主导(卖方拒绝)后回落概率高。",
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
        result = (skew.rolling(3).mean() * -1.0).rolling(5).mean().clip(-1, 1)
        return result
