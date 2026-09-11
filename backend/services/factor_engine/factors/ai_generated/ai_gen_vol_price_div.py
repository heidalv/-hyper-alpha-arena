"""AI因子: 量价背离因子 | 置信:55% | 价格短期涨幅与成交量变化方向背离时预示趋势衰竭。用价格动量与成交量动量的差值衡量：价涨量缩(负背离)预示回落，价跌量增(正背离)预示反弹。"""
import pandas as pd
import numpy as np
from backend.services.factor_engine.factor_base import BaseFactor, FactorMetadata
from backend.services.factor_engine.factor_registry import register_factor


@register_factor()
class VolumePriceDivergence(BaseFactor):
    """价格短期涨幅与成交量变化方向背离时预示趋势衰竭。用价格动量与成交量动量的差值衡量：价涨量缩(负背离)预示回落，价跌量增(正背离)预示反弹。"""

    def get_metadata(self) -> FactorMetadata:
        return FactorMetadata(
            factor_id="ai_gen_vol_price_div",
            name="Volume Price Divergence",
            display_name="量价背离因子",
            description="价格短期涨幅与成交量变化方向背离时预示趋势衰竭。用价格动量与成交量动量的差值衡量：价涨量缩(负背离)预示回落，价跌量增(正背离)预示反弹。",
            category="technical",
            subcategory="volume",
            version="1.0.0-ai",
            author="AI Generated (D7)",
        )

    def calculate(self, data):
        p_mom = data['close'].pct_change(5)
        v_mom = data['volume'].pct_change(5)
        v_std = data['volume'].pct_change().rolling(20).std() + 1e-9
        result = ((p_mom - v_mom / v_std * 0.1)).rolling(3).mean().clip(-1, 1)
        return result
