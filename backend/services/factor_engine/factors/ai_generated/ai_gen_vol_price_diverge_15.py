"""AI因子: 量价背离 | 置信:58% | 价格短期涨幅与成交量变化的背离程度。当价格上涨但成交量萎缩（量价背离）时预示上涨乏力，反之放量上涨确认趋势。用成交量z-score与收益方向交互衡量背离强度。"""
import pandas as pd
import numpy as np
from backend.services.factor_engine.factor_base import BaseFactor, FactorMetadata
from backend.services.factor_engine.factor_registry import register_factor


@register_factor()
class VolumePriceDivergence15(BaseFactor):
    """价格短期涨幅与成交量变化的背离程度。当价格上涨但成交量萎缩（量价背离）时预示上涨乏力，反之放量上涨确认趋势。用成交量z-score与收益方向交互衡量背离强度。"""

    def get_metadata(self) -> FactorMetadata:
        return FactorMetadata(
            factor_id="ai_gen_vol_price_diverge_15",
            name="Volume Price Divergence 15",
            display_name="量价背离",
            description="价格短期涨幅与成交量变化的背离程度。当价格上涨但成交量萎缩（量价背离）时预示上涨乏力，反之放量上涨确认趋势。用成交量z-score与收益方向交互衡量背离强度。",
            category="technical",
            subcategory="volume",
            version="1.0.0-ai",
            author="AI Generated (D7)",
        )

    def calculate(self, data):
        ret = data['close'].pct_change(10)
        vol_ma = data['volume'].rolling(20).mean() + 1e-9
        vol_ratio = data['volume'] / vol_ma
        vol_z = (vol_ratio - vol_ratio.rolling(20).mean()) / (vol_ratio.rolling(20).std() + 1e-9)
        result = (ret * vol_z).rolling(3).mean().clip(-1, 1)
        return result
