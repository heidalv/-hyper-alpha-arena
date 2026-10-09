"""AI因子: 插针不对称反转 | 置信:62% | 利用上下影线不对称度衡量买卖方防守强度：下影主导（买方承接）预示短期反弹，上影主导（卖方拒绝）预示短期回落。用3期滚动均值平滑噪声，输出值域[-1,1]，正值看多、负值看空。"""
import pandas as pd
import numpy as np
from backend.services.factor_engine.factor_base import BaseFactor, FactorMetadata
from backend.services.factor_engine.factor_registry import register_factor


@register_factor()
class PinBarAsymmetryReversal(BaseFactor):
    """利用上下影线不对称度衡量买卖方防守强度：下影主导（买方承接）预示短期反弹，上影主导（卖方拒绝）预示短期回落。用3期滚动均值平滑噪声，输出值域[-1,1]，正值看多、负值看空。"""

    def get_metadata(self) -> FactorMetadata:
        return FactorMetadata(
            factor_id="ai_gen_pinbar_asym_rev",
            name="Pin Bar Asymmetry Reversal",
            display_name="插针不对称反转",
            description="利用上下影线不对称度衡量买卖方防守强度：下影主导（买方承接）预示短期反弹，上影主导（卖方拒绝）预示短期回落。用3期滚动均值平滑噪声，输出值域[-1,1]，正值看多、负值看空。",
            category="technical",
            subcategory="contrarian",
            version="1.0.0-ai",
            author="AI Generated (D7)",
        )

    def calculate(self, data):
        body = (data['close'] - data['open']).abs() + 1e-9
        upper = data['high'] - data[['open','close']].max(axis=1)
        lower = data[['open','close']].min(axis=1) - data['low']
        result = ((lower - upper) / body).rolling(3).mean().clip(-1, 1)
        return result
