"""AI因子: 未知行情波动偏度因子 | 置信:50% | 针对regime=unknown下的多品种亏损，捕捉市场状态不明确时的波动率不对称性。通过正负收益波动差异，识别趋势不明朗时的高风险区间。"""
import pandas as pd
import numpy as np
from backend.services.factor_engine.factor_base import BaseFactor, FactorMetadata
from backend.services.factor_engine.factor_registry import register_factor


@register_factor()
class RegimeunknownVolatilitySkew(BaseFactor):
    """针对regime=unknown下的多品种亏损，捕捉市场状态不明确时的波动率不对称性。通过正负收益波动差异，识别趋势不明朗时的高风险区间。"""

    def get_metadata(self) -> FactorMetadata:
        return FactorMetadata(
            factor_id="ai_gen_regime_unknown_vol_skew",
            name="RegimeUnknown_Volatility_Skew",
            display_name="未知行情波动偏度因子",
            description="针对regime=unknown下的多品种亏损，捕捉市场状态不明确时的波动率不对称性。通过正负收益波动差异，识别趋势不明朗时的高风险区间。",
            category="composite",
            subcategory="volatility",
            version="1.0.0-ai",
            author="AI Generated (D7)",
        )

    def calculate(self, data):
        ret = data['close'].pct_change()
        up_vol = ret[ret > 0].rolling(20).std()
        down_vol = ret[ret < 0].rolling(20).std()
        skew = (up_vol - down_vol) / (up_vol + down_vol + 1e-9)
        vol_ratio = data['volume'].rolling(20).mean() / (data['volume'].rolling(50).mean() + 1e-9)
        result = (skew * vol_ratio).clip(-1, 1)
        return result
