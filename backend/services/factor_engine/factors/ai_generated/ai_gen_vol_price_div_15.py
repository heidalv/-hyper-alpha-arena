"""AI因子: 量价背离因子 | 置信:55% | 价格上涨但成交量未同步放大(量价背离)往往预示动能衰竭，反之放量上涨确认趋势。用价格变化与成交量变化的符号一致性构造背离度。"""
import pandas as pd
import numpy as np
from backend.services.factor_engine.factor_base import BaseFactor, FactorMetadata
from backend.services.factor_engine.factor_registry import register_factor


@register_factor()
class VolumePriceDivergence(BaseFactor):
    """价格上涨但成交量未同步放大(量价背离)往往预示动能衰竭，反之放量上涨确认趋势。用价格变化与成交量变化的符号一致性构造背离度。"""

    def get_metadata(self) -> FactorMetadata:
        return FactorMetadata(
            factor_id="ai_gen_vol_price_div_15",
            name="Volume Price Divergence",
            display_name="量价背离因子",
            description="价格上涨但成交量未同步放大(量价背离)往往预示动能衰竭，反之放量上涨确认趋势。用价格变化与成交量变化的符号一致性构造背离度。",
            category="technical",
            subcategory="volume",
            version="1.0.0-ai",
            author="AI Generated (D7)",
        )

    def calculate(self, data):
        ret = data['close'].pct_change(5)
        vchg = data['volume'].pct_change(5)
        vstd = data['volume'].pct_change().rolling(20).std()
        result = (ret * (vchg / (vstd + 1e-9))).rolling(3).mean().clip(-1, 1)
        return result
