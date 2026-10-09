"""AI因子: 波动率调整动量 | 置信:60% | 用20日收益率除以20日已实现波动率，衡量单位风险下的动量强度，高值代表趋势稳健，低值代表趋势弱或反转风险。"""
import pandas as pd
import numpy as np
from backend.services.factor_engine.factor_base import BaseFactor, FactorMetadata
from backend.services.factor_engine.factor_registry import register_factor


@register_factor()
class VolatilityAdjustedMomentum(BaseFactor):
    """用20日收益率除以20日已实现波动率，衡量单位风险下的动量强度，高值代表趋势稳健，低值代表趋势弱或反转风险。"""

    def get_metadata(self) -> FactorMetadata:
        return FactorMetadata(
            factor_id="ai_gen_vol_momentum_interact",
            name="Volatility Adjusted Momentum",
            display_name="波动率调整动量",
            description="用20日收益率除以20日已实现波动率，衡量单位风险下的动量强度，高值代表趋势稳健，低值代表趋势弱或反转风险。",
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
