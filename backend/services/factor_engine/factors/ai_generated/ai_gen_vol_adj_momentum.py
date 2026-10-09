"""AI因子: 波动率调整动量 | 置信:60% | 20日收益率除以20日已实现波动率，衡量单位风险下的趋势强度。高值表示强劲且稳定的上涨趋势，预期未来继续上涨；负值表示弱势下跌。"""
import pandas as pd
import numpy as np
from backend.services.factor_engine.factor_base import BaseFactor, FactorMetadata
from backend.services.factor_engine.factor_registry import register_factor


@register_factor()
class VolatilityAdjustedMomentum(BaseFactor):
    """20日收益率除以20日已实现波动率，衡量单位风险下的趋势强度。高值表示强劲且稳定的上涨趋势，预期未来继续上涨；负值表示弱势下跌。"""

    def get_metadata(self) -> FactorMetadata:
        return FactorMetadata(
            factor_id="ai_gen_vol_adj_momentum",
            name="Volatility Adjusted Momentum",
            display_name="波动率调整动量",
            description="20日收益率除以20日已实现波动率，衡量单位风险下的趋势强度。高值表示强劲且稳定的上涨趋势，预期未来继续上涨；负值表示弱势下跌。",
            category="technical",
            subcategory="momentum",
            version="1.0.0-ai",
            author="AI Generated (D7)",
        )

    def calculate(self, data):
        ret = data['close'].pct_change(20)
        vol = data['close'].pct_change().rolling(20).std()
        result = (ret / (vol + 1e-9)).clip(-1, 1)
        return result
