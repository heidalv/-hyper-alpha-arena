"""AI因子: 量价背离 | 置信:55% | 价格上涨但成交量萎缩(量价背离)预示动能衰竭，给负分；价格下跌但放量(恐慌抛售)可能反转，给正分。用收益与成交量变化的符号背离构造。"""
import pandas as pd
import numpy as np
from backend.services.factor_engine.factor_base import BaseFactor, FactorMetadata
from backend.services.factor_engine.factor_registry import register_factor


@register_factor()
class VolumePriceDivergence(BaseFactor):
    """价格上涨但成交量萎缩(量价背离)预示动能衰竭，给负分；价格下跌但放量(恐慌抛售)可能反转，给正分。用收益与成交量变化的符号背离构造。"""

    def get_metadata(self) -> FactorMetadata:
        return FactorMetadata(
            factor_id="ai_gen_vol_price_divergence",
            name="Volume Price Divergence",
            display_name="量价背离",
            description="价格上涨但成交量萎缩(量价背离)预示动能衰竭，给负分；价格下跌但放量(恐慌抛售)可能反转，给正分。用收益与成交量变化的符号背离构造。",
            category="technical",
            subcategory="volume",
            version="1.0.0-ai",
            author="AI Generated (D7)",
        )

    def calculate(self, data):
        ret = data['close'].pct_change(5)
        vol_chg = data['volume'].pct_change(5)
        vol_z = (vol_chg - vol_chg.rolling(20).mean()) / (vol_chg.rolling(20).std() + 1e-9)
        result = (-ret * vol_z).rolling(5).mean().clip(-1, 1)
        return result
