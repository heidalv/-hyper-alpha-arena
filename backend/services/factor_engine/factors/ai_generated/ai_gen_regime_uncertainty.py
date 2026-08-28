"""AI因子: 市场状态不确定性因子 | 置信:50% | 针对regime=unknown的普遍亏损，通过价格动量与成交量确认度的背离衡量市场状态的不确定性。当价格趋势明显但成交量确认不足时，因子值降低，提示当前regime不明确，应减少持仓或缩短持仓周期。"""
import pandas as pd
import numpy as np
from backend.services.factor_engine.factor_base import BaseFactor, FactorMetadata
from backend.services.factor_engine.factor_registry import register_factor


@register_factor()
class RegimeUncertaintyDivergence(BaseFactor):
    """针对regime=unknown的普遍亏损，通过价格动量与成交量确认度的背离衡量市场状态的不确定性。当价格趋势明显但成交量确认不足时，因子值降低，提示当前regime不明确，应减少持仓或缩短持仓周期。"""

    def get_metadata(self) -> FactorMetadata:
        return FactorMetadata(
            factor_id="ai_gen_regime_uncertainty",
            name="Regime Uncertainty Divergence",
            display_name="市场状态不确定性因子",
            description="针对regime=unknown的普遍亏损，通过价格动量与成交量确认度的背离衡量市场状态的不确定性。当价格趋势明显但成交量确认不足时，因子值降低，提示当前regime不明确，应减少持仓或缩短持仓周期。",
            category="composite",
            subcategory="trend",
            version="1.0.0-ai",
            author="AI Generated (D7)",
        )

    def calculate(self, data):
        ret = data['close'].pct_change(10)
        vol_confirm = data['volume'].rolling(10).mean() / (data['volume'].rolling(50).mean() + 1e-9)
        price_confirm = data['close'].rolling(10).std() / (data['close'].rolling(50).std() + 1e-9)
        divergence = abs(ret) * (1 - (vol_confirm / (price_confirm + 1e-9)).clip(0, 2))
        result = (-divergence * 2).clip(-1, 1)
        return result
