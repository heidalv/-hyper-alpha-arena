"""AI因子: 波动率调整反转 | 置信:60% | 短期收益相对已实现波动率的标准化偏离，衡量超买超卖强度。高波动下短期偏离更易均值回归，低波动下偏离更可能延续。用波动率倒数加权短期收益并反向，捕捉波动率聚集环境下的反转 alpha。"""
import pandas as pd
import numpy as np
from backend.services.factor_engine.factor_base import BaseFactor, FactorMetadata
from backend.services.factor_engine.factor_registry import register_factor


@register_factor()
class VolatilityAdjustedReversal(BaseFactor):
    """短期收益相对已实现波动率的标准化偏离，衡量超买超卖强度。高波动下短期偏离更易均值回归，低波动下偏离更可能延续。用波动率倒数加权短期收益并反向，捕捉波动率聚集环境下的反转 alpha。"""

    def get_metadata(self) -> FactorMetadata:
        return FactorMetadata(
            factor_id="ai_gen_vol_adj_rev",
            name="Volatility Adjusted Reversal",
            display_name="波动率调整反转",
            description="短期收益相对已实现波动率的标准化偏离，衡量超买超卖强度。高波动下短期偏离更易均值回归，低波动下偏离更可能延续。用波动率倒数加权短期收益并反向，捕捉波动率聚集环境下的反转 alpha。",
            category="technical",
            subcategory="volatility",
            version="1.0.0-ai",
            author="AI Generated (D7)",
        )

    def calculate(self, data):
        ret = data['close'].pct_change(5)
        vol = data['close'].pct_change().rolling(20).std()
        vol_ratio = vol / (data['close'].pct_change().rolling(60).std() + 1e-9)
        z = ret / (vol + 1e-9)
        result = (-z * vol_ratio.clip(0, 3)).clip(-1, 1)
        return result
