"""AI因子: 插针影线不对称反转 | 置信:62% | 计算下影线与上影线的不对称度(lower-upper)/body，反映买方防守与卖方拒绝的相对强度；取3期滚动均值平滑噪声，正值代表下影主导（买方承接），预示短期反弹概率更高，负值代表上影主导（卖方压制），预示短期回落。"""
import pandas as pd
import numpy as np
from backend.services.factor_engine.factor_base import BaseFactor, FactorMetadata
from backend.services.factor_engine.factor_registry import register_factor


@register_factor()
class PinBarAsymmetryReversal(BaseFactor):
    """计算下影线与上影线的不对称度(lower-upper)/body，反映买方防守与卖方拒绝的相对强度；取3期滚动均值平滑噪声，正值代表下影主导（买方承接），预示短期反弹概率更高，负值代表上影主导（卖方压制），预示短期回落。"""

    def get_metadata(self) -> FactorMetadata:
        return FactorMetadata(
            factor_id="ai_gen_pin_reversal_001",
            name="Pin Bar Asymmetry Reversal",
            display_name="插针影线不对称反转",
            description="计算下影线与上影线的不对称度(lower-upper)/body，反映买方防守与卖方拒绝的相对强度；取3期滚动均值平滑噪声，正值代表下影主导（买方承接），预示短期反弹概率更高，负值代表上影主导（卖方压制），预示短期回落。",
            category="technical",
            subcategory="mean_reversion",
            version="1.0.0-ai",
            author="AI Generated (D7)",
        )

    def calculate(self, data):
        body = (data['close'] - data['open']).abs() + 1e-9
        upper = data['high'] - data[['open','close']].max(axis=1)
        lower = data[['open','close']].min(axis=1) - data['low']
        result = ((lower - upper) / body).rolling(3).mean().clip(-1, 1)
        return result
