"""AI因子: 插针影线不对称反转 | 置信:62% | 计算下影线与上影线相对实体的大小差异（下影主导=买方承接，上影主导=卖方拒绝），取3期滚动均值后取负号，捕捉插针后的短期均值回归方向。高值代表近期下影主导（超卖承接），预期反弹。"""
import pandas as pd
import numpy as np
from backend.services.factor_engine.factor_base import BaseFactor, FactorMetadata
from backend.services.factor_engine.factor_registry import register_factor


@register_factor()
class PinBarAsymmetryReversal(BaseFactor):
    """计算下影线与上影线相对实体的大小差异（下影主导=买方承接，上影主导=卖方拒绝），取3期滚动均值后取负号，捕捉插针后的短期均值回归方向。高值代表近期下影主导（超卖承接），预期反弹。"""

    def get_metadata(self) -> FactorMetadata:
        return FactorMetadata(
            factor_id="ai_gen_pinbar_asym_rev",
            name="Pin Bar Asymmetry Reversal",
            display_name="插针影线不对称反转",
            description="计算下影线与上影线相对实体的大小差异（下影主导=买方承接，上影主导=卖方拒绝），取3期滚动均值后取负号，捕捉插针后的短期均值回归方向。高值代表近期下影主导（超卖承接），预期反弹。",
            category="technical",
            subcategory="mean_reversion",
            version="1.0.0-ai",
            author="AI Generated (D7)",
        )

    def calculate(self, data):
        body = (data['close'] - data['open']).abs() + 1e-9
        upper = data['high'] - data[['open','close']].max(axis=1)
        lower = data[['open','close']].min(axis=1) - data['low']
        result = (-(lower - upper) / body).rolling(3).mean().clip(-1, 1)
        return result
