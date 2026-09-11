"""AI因子: 量价背离 | 置信:58% | 价格短期涨幅与成交量变化方向的背离程度。价格上涨但缩量（量价背离）预示动能不足，因子取负；价格下跌但放量可能为恐慌抛售后的反转信号。"""
import pandas as pd
import numpy as np
from backend.services.factor_engine.factor_base import BaseFactor, FactorMetadata
from backend.services.factor_engine.factor_registry import register_factor


@register_factor()
class VolumePriceDivergence(BaseFactor):
    """价格短期涨幅与成交量变化方向的背离程度。价格上涨但缩量（量价背离）预示动能不足，因子取负；价格下跌但放量可能为恐慌抛售后的反转信号。"""

    def get_metadata(self) -> FactorMetadata:
        return FactorMetadata(
            factor_id="ai_gen_vol_price_divergence",
            name="Volume Price Divergence",
            display_name="量价背离",
            description="价格短期涨幅与成交量变化方向的背离程度。价格上涨但缩量（量价背离）预示动能不足，因子取负；价格下跌但放量可能为恐慌抛售后的反转信号。",
            category="technical",
            subcategory="volume",
            version="1.0.0-ai",
            author="AI Generated (D7)",
        )

    def calculate(self, data):
        ret = data['close'].pct_change(5)
        volchg = data['volume'].pct_change(5)
        volstd = data['volume'].pct_change().rolling(20).std() + 1e-9
        result = (-ret * volchg / volstd).clip(-1, 1)
        return result
