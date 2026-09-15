"""AI因子: 量价背离 | 置信:58% | 价格变化与成交量变化的背离程度。价格上行但成交量萎缩（量价背离）预示动能不足，看跌；价格下行但放量（恐慌抛售）后可能反转。用成交量 z-score 与收益方向交互。"""
import pandas as pd
import numpy as np
from backend.services.factor_engine.factor_base import BaseFactor, FactorMetadata
from backend.services.factor_engine.factor_registry import register_factor


@register_factor()
class VolumePriceDivergence(BaseFactor):
    """价格变化与成交量变化的背离程度。价格上行但成交量萎缩（量价背离）预示动能不足，看跌；价格下行但放量（恐慌抛售）后可能反转。用成交量 z-score 与收益方向交互。"""

    def get_metadata(self) -> FactorMetadata:
        return FactorMetadata(
            factor_id="ai_gen_vol_price_divergence",
            name="Volume-Price Divergence",
            display_name="量价背离",
            description="价格变化与成交量变化的背离程度。价格上行但成交量萎缩（量价背离）预示动能不足，看跌；价格下行但放量（恐慌抛售）后可能反转。用成交量 z-score 与收益方向交互。",
            category="technical",
            subcategory="volume",
            version="1.0.0-ai",
            author="AI Generated (D7)",
        )

    def calculate(self, data):
        ret = data['close'].pct_change(5)
        vol_chg = data['volume'].pct_change(5)
        vol_std = data['volume'].pct_change().rolling(20).std()
        vol_z = vol_chg / (vol_std + 1e-9)
        result = (ret * (1 - vol_z.rank(pct=True))).clip(-1, 1)
        return result
