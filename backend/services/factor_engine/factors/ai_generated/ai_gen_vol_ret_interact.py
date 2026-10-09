"""AI因子: 波动收益交互 | 置信:55% | 已实现波动率与短期收益的交互项：低波动下的上涨更可能延续(趋势确认)，高波动下的上涨更可能透支(反转)。用波动率分位调节动量方向，捕捉波动率聚集环境下的收益可预测性。"""
import pandas as pd
import numpy as np
from backend.services.factor_engine.factor_base import BaseFactor, FactorMetadata
from backend.services.factor_engine.factor_registry import register_factor


@register_factor()
class VolatilityReturnInteraction(BaseFactor):
    """已实现波动率与短期收益的交互项：低波动下的上涨更可能延续(趋势确认)，高波动下的上涨更可能透支(反转)。用波动率分位调节动量方向，捕捉波动率聚集环境下的收益可预测性。"""

    def get_metadata(self) -> FactorMetadata:
        return FactorMetadata(
            factor_id="ai_gen_vol_ret_interact",
            name="Volatility Return Interaction",
            display_name="波动收益交互",
            description="已实现波动率与短期收益的交互项：低波动下的上涨更可能延续(趋势确认)，高波动下的上涨更可能透支(反转)。用波动率分位调节动量方向，捕捉波动率聚集环境下的收益可预测性。",
            category="technical",
            subcategory="volatility",
            version="1.0.0-ai",
            author="AI Generated (D7)",
        )

    def calculate(self, data):
        ret = data['close'].pct_change(10)
        vol = data['close'].pct_change().rolling(20).std()
        vol_rank = vol.rolling(60).rank(pct=True)
        result = (ret * (0.5 - vol_rank)).clip(-1, 1)
        return result
