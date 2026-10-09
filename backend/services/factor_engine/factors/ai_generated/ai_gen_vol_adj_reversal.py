"""AI因子: 波动率调整的短期反转 | 置信:60% | 短期收益率除以近期已实现波动率，衡量单位风险下的超买超卖程度。取反号后作为反转因子：波动调整后涨幅过大者未来回落概率高，跌幅过大者未来反弹概率高。"""
import pandas as pd
import numpy as np
from backend.services.factor_engine.factor_base import BaseFactor, FactorMetadata
from backend.services.factor_engine.factor_registry import register_factor


@register_factor()
class VolatilityAdjustedShortReversal(BaseFactor):
    """短期收益率除以近期已实现波动率，衡量单位风险下的超买超卖程度。取反号后作为反转因子：波动调整后涨幅过大者未来回落概率高，跌幅过大者未来反弹概率高。"""

    def get_metadata(self) -> FactorMetadata:
        return FactorMetadata(
            factor_id="ai_gen_vol_adj_reversal",
            name="Volatility Adjusted Short Reversal",
            display_name="波动率调整的短期反转",
            description="短期收益率除以近期已实现波动率，衡量单位风险下的超买超卖程度。取反号后作为反转因子：波动调整后涨幅过大者未来回落概率高，跌幅过大者未来反弹概率高。",
            category="technical",
            subcategory="mean_reversion",
            version="1.0.0-ai",
            author="AI Generated (D7)",
        )

    def calculate(self, data):
        ret = data['close'].pct_change(5)
        vol = data['close'].pct_change().rolling(20).std() + 1e-9
        result = (-(ret / vol)).clip(-1, 1)
        return result
