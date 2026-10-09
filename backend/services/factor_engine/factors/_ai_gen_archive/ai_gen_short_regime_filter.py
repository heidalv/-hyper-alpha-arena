"""AI因子: 空头未知行情过滤 | 置信:60% | 基于价格相对长期均线位置和波动率状态，识别未知行情下做空风险高的环境。当价格处于长期均线下方且波动率收缩时，做空容易因反弹止损或超时亏损，该因子在此时给出负值（避免做空），在趋势明确时给出正值。"""
import pandas as pd
import numpy as np
from backend.services.factor_engine.factor_base import BaseFactor, FactorMetadata
from backend.services.factor_engine.factor_registry import register_factor


@register_factor()
class ShortRegimeFilter(BaseFactor):
    """基于价格相对长期均线位置和波动率状态，识别未知行情下做空风险高的环境。当价格处于长期均线下方且波动率收缩时，做空容易因反弹止损或超时亏损，该因子在此时给出负值（避免做空），在趋势明确时给出正值。"""

    def get_metadata(self) -> FactorMetadata:
        return FactorMetadata(
            factor_id="ai_gen_short_regime_filter",
            name="Short_Regime_Filter",
            display_name="空头未知行情过滤",
            description="基于价格相对长期均线位置和波动率状态，识别未知行情下做空风险高的环境。当价格处于长期均线下方且波动率收缩时，做空容易因反弹止损或超时亏损，该因子在此时给出负值（避免做空），在趋势明确时给出正值。",
            category="composite",
            subcategory="mean_reversion",
            version="1.0.0-ai",
            author="AI Generated (D7)",
        )

    def calculate(self, data):
        ret = data['close'].pct_change(20)
        ma_long = data['close'].rolling(100).mean()
        price_vs_ma = (data['close'] - ma_long) / (ma_long + 1e-9)
        vol_short = data['close'].pct_change().rolling(10).std()
        vol_long = data['close'].pct_change().rolling(50).std()
        vol_ratio = vol_short / (vol_long + 1e-9)
        result = (price_vs_ma * -1 + (vol_ratio - 1) * 0.5).clip(-1, 1)
        return result
